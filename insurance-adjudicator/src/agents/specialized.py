"""
Specialized Agents for Insurance Adjudication
Each agent handles a specific aspect of the adjudication process
"""

import json
import logging
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, List
from uuid import UUID

from .base import (
    BaseAgent, AgentContext, AgentResult, AgentTask, AgentCapability
)
from ..models.claim import (
    Claim, Document, Policy, CoverageItem, FraudIndicator,
    AdjudicationDecision, DecisionReason, ClaimStatus
)
from ..config.settings import settings
from ..core.utils import (
    parse_json_from_llm_response,
    ensure_timezone_aware,
    is_round_number,
)

logger = logging.getLogger(__name__)


class DocumentExtractionAgent(BaseAgent):
    """
    Agent responsible for extracting and validating information from claim documents.
    Uses OCR and NLP to extract relevant data from various document types.
    """
    
    def __init__(self, llm_client: Any = None, tools: Optional[List[Any]] = None):
        super().__init__(
            name="document_extraction",
            description="Extracts and validates information from claim documents including "
                       "medical records, police reports, invoices, and repair estimates.",
            llm_client=llm_client,
            tools=tools,
        )
        
        self.capabilities = [
            AgentCapability(
                name="extract_document_data",
                description="Extract structured data from unstructured documents",
                input_schema={"document_id": "UUID", "document_type": "string"},
                output_schema={"extracted_fields": "dict", "confidence": "float"},
            ),
            AgentCapability(
                name="validate_document",
                description="Validate document authenticity and completeness",
                input_schema={"document_id": "UUID"},
                output_schema={"is_valid": "bool", "issues": "list"},
            ),
            AgentCapability(
                name="cross_reference",
                description="Cross-reference data across multiple documents",
                input_schema={"document_ids": "list[UUID]"},
                output_schema={"discrepancies": "list", "consistent": "bool"},
            ),
        ]
    
    async def execute(self, context: AgentContext, task: AgentTask) -> AgentResult:
        async with self.execution_context(task):
            documents = context.claim.documents
            
            if not documents:
                return self.create_result(
                    task=task,
                    success=True,
                    output={"extracted_data": {}, "validation_results": []},
                    reasoning="No documents to process",
                    confidence=1.0,
                )
            
            extracted_data = {}
            validation_results = []
            overall_confidence = 0.0
            
            for doc in documents:
                # Extract data from document
                extraction = await self._extract_from_document(context, doc)
                extracted_data[str(doc.id)] = extraction
                
                # Validate document
                validation = await self._validate_document(context, doc, extraction)
                validation_results.append(validation)
                
                overall_confidence += extraction.get("confidence", 0)
            
            overall_confidence /= len(documents) if documents else 1
            
            # Cross-reference all documents
            cross_ref_result = await self._cross_reference_documents(
                context, documents, extracted_data
            )
            
            # Update shared memory
            await self.update_shared_memory(context, "extracted_documents", extracted_data)
            await self.update_shared_memory(context, "document_validations", validation_results)
            await self.update_shared_memory(context, "cross_reference", cross_ref_result)
            
            return self.create_result(
                task=task,
                success=True,
                output={
                    "extracted_data": extracted_data,
                    "validation_results": validation_results,
                    "cross_reference": cross_ref_result,
                    "document_count": len(documents),
                },
                reasoning=f"Processed {len(documents)} documents with "
                         f"{overall_confidence:.2%} average extraction confidence",
                confidence=overall_confidence,
                next_actions=["policy_analysis", "fraud_detection"]
                            if overall_confidence >= 0.7 else ["request_documents"],
            )
    
    async def _extract_from_document(
        self, context: AgentContext, doc: Document
    ) -> Dict[str, Any]:
        """Extract structured data from a document"""
        
        extraction_prompt = f"""Analyze the following document and extract all relevant information.

Document Type: {doc.document_type}
Filename: {doc.filename}
Content: {doc.extracted_text or "Content not yet extracted"}

Extract the following based on document type:
- For medical records: diagnosis, treatment dates, provider info, costs
- For invoices: line items, amounts, dates, vendor info
- For police reports: incident details, date/time, parties involved, damages
- For repair estimates: items, labor costs, parts costs, total

Return a structured JSON with extracted fields and your confidence level (0-1) for each field.
"""
        
        response = await self.think(context, extraction_prompt)

        # Parse LLM response into structured data using robust parser
        parsed = parse_json_from_llm_response(response, default={
            "raw_response": response,
            "confidence": 0.5,
            "needs_manual_review": True
        })

        # Ensure confidence is present
        if "confidence" not in parsed:
            parsed["confidence"] = 0.5

        return parsed
    
    async def _validate_document(
        self, context: AgentContext, doc: Document, extraction: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate document authenticity and completeness"""

        issues = []
        is_valid = True

        # Check document age - ensure timezone-aware comparison
        now_utc = datetime.now(timezone.utc)
        doc_uploaded = ensure_timezone_aware(doc.uploaded_at)
        doc_age_days = (now_utc - doc_uploaded).days
        if doc_age_days > 365:
            issues.append("Document is over 1 year old")
        
        # Check extraction confidence
        if extraction.get("confidence", 0) < 0.6:
            issues.append("Low extraction confidence - may need clearer document")
            is_valid = False
        
        # Check for required fields based on document type
        required_fields = self._get_required_fields(doc.document_type)
        missing_fields = [
            f for f in required_fields 
            if f not in extraction or extraction[f] is None
        ]
        if missing_fields:
            issues.append(f"Missing required fields: {', '.join(missing_fields)}")
            is_valid = False
        
        return {
            "document_id": str(doc.id),
            "document_type": doc.document_type,
            "is_valid": is_valid,
            "issues": issues,
            "validation_timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def _cross_reference_documents(
        self,
        context: AgentContext,
        documents: List[Document],
        extracted_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Cross-reference data across multiple documents"""
        
        discrepancies = []
        
        # Check for date consistency
        dates = []
        for doc_id, data in extracted_data.items():
            if "date" in data:
                dates.append((doc_id, data["date"]))
        
        # Check for amount consistency
        amounts = []
        for doc_id, data in extracted_data.items():
            if "total_amount" in data:
                amounts.append((doc_id, data["total_amount"]))
        
        # Simple discrepancy detection
        if len(set(d[1] for d in dates)) > 1:
            discrepancies.append({
                "type": "date_mismatch",
                "details": "Documents show different incident dates",
                "severity": "medium"
            })
        
        return {
            "discrepancies": discrepancies,
            "consistent": len(discrepancies) == 0,
            "documents_analyzed": len(documents)
        }
    
    def _get_required_fields(self, document_type: str) -> List[str]:
        """Get required fields for a document type"""
        requirements = {
            "medical_record": ["date", "provider", "diagnosis"],
            "invoice": ["date", "vendor", "amount"],
            "police_report": ["incident_date", "report_number"],
            "repair_estimate": ["date", "items", "total_amount"],
        }
        return requirements.get(document_type, [])


class PolicyAnalysisAgent(BaseAgent):
    """
    Agent responsible for analyzing policy terms and coverage applicability.
    Determines if the claim is covered under the policy.
    """
    
    def __init__(self, llm_client: Any = None, tools: Optional[List[Any]] = None):
        super().__init__(
            name="policy_analysis",
            description="Analyzes insurance policies to determine coverage applicability, "
                       "exclusions, limits, and deductibles for submitted claims.",
            llm_client=llm_client,
            tools=tools,
        )
        
        self.capabilities = [
            AgentCapability(
                name="check_coverage",
                description="Verify if claim is covered under policy",
                input_schema={"claim_type": "string", "policy_id": "UUID"},
                output_schema={"is_covered": "bool", "applicable_coverage": "dict"},
            ),
            AgentCapability(
                name="calculate_limits",
                description="Calculate coverage limits and remaining amounts",
                input_schema={"policy_id": "UUID", "claim_amount": "decimal"},
                output_schema={"coverage_limit": "decimal", "remaining": "decimal"},
            ),
            AgentCapability(
                name="identify_exclusions",
                description="Identify any policy exclusions that apply",
                input_schema={"claim_details": "dict", "policy_id": "UUID"},
                output_schema={"exclusions": "list", "applies": "bool"},
            ),
        ]
    
    async def execute(self, context: AgentContext, task: AgentTask) -> AgentResult:
        async with self.execution_context(task):
            claim = context.claim
            policy = claim.policy
            
            if not policy:
                return self.create_result(
                    task=task,
                    success=False,
                    output={},
                    reasoning="No policy found for claim",
                    confidence=0.0,
                    requires_human_review=True,
                    human_review_reasons=["Policy not found in system"],
                )
            
            # Check policy validity
            validity_check = self._check_policy_validity(policy, claim)
            if not validity_check["is_valid"]:
                return self.create_result(
                    task=task,
                    success=True,
                    output=validity_check,
                    reasoning=validity_check["reason"],
                    confidence=0.95,
                    next_actions=["deny_claim"],
                )
            
            # Analyze coverage applicability
            coverage_analysis = await self._analyze_coverage(context, claim, policy)
            
            # Check for exclusions
            exclusion_analysis = await self._check_exclusions(context, claim, policy)
            
            # Calculate payable amount
            payment_calculation = self._calculate_payment(
                claim, policy, coverage_analysis, exclusion_analysis
            )
            
            # Store results in shared memory
            await self.update_shared_memory(context, "policy_analysis", {
                "validity": validity_check,
                "coverage": coverage_analysis,
                "exclusions": exclusion_analysis,
                "payment": payment_calculation,
            })
            
            overall_confidence = (
                coverage_analysis.get("confidence", 0) * 0.5 +
                exclusion_analysis.get("confidence", 0) * 0.3 +
                0.2  # Base confidence for calculations
            )
            
            return self.create_result(
                task=task,
                success=True,
                output={
                    "validity_check": validity_check,
                    "coverage_analysis": coverage_analysis,
                    "exclusion_analysis": exclusion_analysis,
                    "payment_calculation": payment_calculation,
                    "recommendation": self._generate_recommendation(
                        coverage_analysis, exclusion_analysis, payment_calculation
                    ),
                },
                reasoning=f"Policy analysis complete. Coverage applicable: "
                         f"{coverage_analysis.get('is_covered', False)}. "
                         f"Recommended payment: ${payment_calculation.get('recommended_amount', 0)}",
                confidence=overall_confidence,
                next_actions=["fraud_detection", "final_decision"],
            )
    
    # Define compatible claim types for each policy type
    POLICY_CLAIM_COMPATIBILITY = {
        "home": {"home", "property", "liability"},
        "property": {"property", "home"},
        "auto": {"auto", "liability"},
        "health": {"health"},
        "life": {"life"},
        "disability": {"disability", "health"},
        "liability": {"liability"},
        "workers_compensation": {"workers_compensation", "disability", "health"},
    }

    def _check_policy_validity(self, policy: Policy, claim: Claim) -> Dict[str, Any]:
        """Check if policy is valid for this claim"""

        if not policy.is_valid:
            return {
                "is_valid": False,
                "reason": "Policy is not active or has expired",
                "recommendation": DecisionReason.POLICY_LAPSED.value
            }

        # Check if loss date is within policy period
        if claim.date_of_loss < policy.effective_date:
            return {
                "is_valid": False,
                "reason": "Loss occurred before policy effective date",
                "recommendation": DecisionReason.POLICY_LAPSED.value
            }

        if claim.date_of_loss > policy.expiration_date:
            return {
                "is_valid": False,
                "reason": "Loss occurred after policy expiration",
                "recommendation": DecisionReason.POLICY_LAPSED.value
            }

        # Check claim type compatibility with policy type (not strict equality)
        policy_type_str = policy.policy_type.value if hasattr(policy.policy_type, 'value') else str(policy.policy_type)
        claim_type_str = claim.claim_type.value if hasattr(claim.claim_type, 'value') else str(claim.claim_type)

        compatible_claim_types = self.POLICY_CLAIM_COMPATIBILITY.get(
            policy_type_str.lower(),
            {policy_type_str.lower()}  # Default to exact match if not in mapping
        )

        if claim_type_str.lower() not in compatible_claim_types:
            return {
                "is_valid": False,
                "reason": f"Claim type ({claim.claim_type}) is not compatible with "
                         f"policy type ({policy.policy_type}). "
                         f"Compatible types: {compatible_claim_types}",
                "recommendation": DecisionReason.COVERAGE_EXCLUDED.value
            }

        return {
            "is_valid": True,
            "reason": "Policy is valid and active for this claim",
            "policy_number": policy.policy_number,
            "days_until_expiration": policy.days_until_expiration
        }
    
    async def _analyze_coverage(
        self, context: AgentContext, claim: Claim, policy: Policy
    ) -> Dict[str, Any]:
        """Analyze coverage applicability"""
        
        applicable_coverages = []
        total_coverage_available = Decimal("0")
        
        for coverage in policy.coverages:
            # Check if this coverage applies to the claim items
            for item in claim.items:
                if self._coverage_matches_item(coverage, item):
                    applicable_coverages.append({
                        "coverage_name": coverage.name,
                        "coverage_type": coverage.coverage_type,
                        "limit": str(coverage.limit),
                        "deductible": str(coverage.deductible),
                        "item_id": str(item.id),
                        "item_description": item.description,
                    })
                    total_coverage_available += coverage.limit
        
        is_covered = len(applicable_coverages) > 0
        
        # Use LLM for complex coverage determination
        if claim.description and is_covered:
            coverage_prompt = f"""Analyze this insurance claim for coverage determination:

Claim Description: {claim.description}
Claim Amount: ${claim.total_amount_claimed}
Claim Type: {claim.claim_type}

Policy Coverages:
{json.dumps([{"name": c.name, "type": c.coverage_type, "limit": str(c.limit)} for c in policy.coverages], indent=2)}

Determine:
1. Is this claim clearly covered under the policy?
2. Are there any coverage ambiguities?
3. What is your confidence level (0-1)?

Respond in JSON format.
"""
            
            llm_analysis = await self.think(context, coverage_prompt)
            parsed = parse_json_from_llm_response(llm_analysis, default={"confidence": 0.7})
            confidence = parsed.get("confidence", 0.7)
        else:
            confidence = 0.9 if is_covered else 0.95
        
        return {
            "is_covered": is_covered,
            "applicable_coverages": applicable_coverages,
            "total_coverage_available": str(total_coverage_available),
            "confidence": confidence,
        }
    
    async def _check_exclusions(
        self, context: AgentContext, claim: Claim, policy: Policy
    ) -> Dict[str, Any]:
        """Check if any exclusions apply to this claim"""
        
        applying_exclusions = []
        
        exclusion_prompt = f"""Review these policy exclusions against the claim:

Claim Description: {claim.description}
Claim Type: {claim.claim_type}
Date of Loss: {claim.date_of_loss}

Policy Exclusions:
{json.dumps(policy.exclusions, indent=2)}

For each exclusion, determine if it applies to this claim.
Return JSON with format: {{"exclusions": [{{"exclusion": "text", "applies": true/false, "reason": "explanation"}}]}}
"""
        
        llm_response = await self.think(context, exclusion_prompt)

        parsed = parse_json_from_llm_response(llm_response, default={"exclusions": []})
        for exc in parsed.get("exclusions", []):
            if exc.get("applies"):
                applying_exclusions.append(exc)
        
        return {
            "exclusions_apply": len(applying_exclusions) > 0,
            "applying_exclusions": applying_exclusions,
            "confidence": 0.85 if applying_exclusions else 0.9,
        }
    
    # Extended category mapping for coverage matching
    COVERAGE_CATEGORY_MAPPING = {
        "medical": {
            "keywords": ["medical", "hospital", "doctor", "treatment", "surgery", "diagnosis",
                        "prescription", "therapy", "clinic", "emergency", "ambulance", "health",
                        "physician", "nurse", "medication", "injury", "illness"],
            "categories": ["medical", "health", "healthcare", "treatment"]
        },
        "property": {
            "keywords": ["repair", "replacement", "damage", "broken", "destroyed", "stolen",
                        "vandalism", "fire", "water", "flood", "storm", "theft", "burglary",
                        "appliance", "furniture", "electronics", "structure", "building"],
            "categories": ["property", "home", "dwelling", "contents", "personal_property"]
        },
        "collision": {
            "keywords": ["collision", "accident", "crash", "impact", "hit", "vehicle",
                        "car", "auto", "truck", "motorcycle", "fender", "bumper", "dent"],
            "categories": ["collision", "auto", "vehicle", "car"]
        },
        "comprehensive": {
            "keywords": ["comprehensive", "theft", "vandalism", "weather", "hail", "flood",
                        "fire", "animal", "glass", "windshield", "natural"],
            "categories": ["comprehensive", "auto", "vehicle"]
        },
        "liability": {
            "keywords": ["legal", "settlement", "lawsuit", "attorney", "court", "judgment",
                        "damages", "injury", "third_party", "negligence", "fault"],
            "categories": ["liability", "legal", "third_party"]
        },
        "disability": {
            "keywords": ["disability", "income", "unable_to_work", "impairment", "wages",
                        "earnings", "occupation", "employment"],
            "categories": ["disability", "income", "wage"]
        },
    }

    def _coverage_matches_item(self, coverage: CoverageItem, item: Any) -> bool:
        """Check if a coverage applies to a claim item using fuzzy matching"""
        coverage_type_lower = coverage.coverage_type.lower()
        item_desc_lower = item.description.lower()
        item_category_lower = item.category.lower() if item.category else ""

        # Direct category match
        if coverage_type_lower in item_category_lower or item_category_lower in coverage_type_lower:
            return True

        # Check against extended mapping
        for mapping_type, mapping_data in self.COVERAGE_CATEGORY_MAPPING.items():
            # Check if coverage type matches this mapping
            if mapping_type in coverage_type_lower or coverage_type_lower in mapping_type:
                keywords = mapping_data["keywords"]
                categories = mapping_data["categories"]

                # Check if item description contains any keywords
                if any(keyword in item_desc_lower for keyword in keywords):
                    return True

                # Check if item category matches
                if any(cat in item_category_lower for cat in categories):
                    return True

        # Fallback: check for any word overlap between coverage name and item description
        coverage_words = set(coverage.name.lower().split())
        item_words = set(item_desc_lower.split())
        # If at least 2 significant words match (excluding common words)
        common_words = {"the", "a", "an", "and", "or", "for", "of", "to", "in", "on", "with"}
        meaningful_overlap = (coverage_words - common_words) & (item_words - common_words)
        if len(meaningful_overlap) >= 1:
            return True

        return False
    
    def _calculate_payment(
        self,
        claim: Claim,
        policy: Policy,
        coverage_analysis: Dict[str, Any],
        exclusion_analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calculate the recommended payment amount"""

        if not coverage_analysis.get("is_covered"):
            return {
                "recommended_amount": "0",
                "reason": DecisionReason.COVERAGE_EXCLUDED.value,
                "breakdown": {}
            }

        if exclusion_analysis.get("exclusions_apply"):
            return {
                "recommended_amount": "0",
                "reason": DecisionReason.COVERAGE_EXCLUDED.value,
                "breakdown": {"exclusion_applied": True}
            }

        claimed_amount = claim.total_amount_claimed
        applicable_coverages = coverage_analysis.get("applicable_coverages", [])

        # Calculate payment using per-coverage deductibles
        total_payable = Decimal("0")
        breakdown_items = []

        if applicable_coverages:
            # Calculate payment per applicable coverage
            for coverage_info in applicable_coverages:
                coverage_limit = Decimal(coverage_info.get("limit", "0"))
                coverage_deductible = Decimal(coverage_info.get("deductible", "0"))

                # Find matching claim item amount
                item_id = coverage_info.get("item_id")
                item_amount = Decimal("0")
                for item in claim.items:
                    if str(item.id) == item_id:
                        item_amount = item.amount_claimed
                        break

                # If no specific item, distribute claim amount proportionally
                if item_amount == 0:
                    item_amount = claimed_amount / max(len(applicable_coverages), 1)

                # Apply per-coverage deductible
                after_deductible = max(Decimal("0"), item_amount - coverage_deductible)
                coverage_payment = min(after_deductible, coverage_limit)
                total_payable += coverage_payment

                breakdown_items.append({
                    "coverage": coverage_info.get("coverage_name", "Unknown"),
                    "item_amount": str(item_amount),
                    "deductible": str(coverage_deductible),
                    "after_deductible": str(after_deductible),
                    "coverage_limit": str(coverage_limit),
                    "payment": str(coverage_payment),
                })
        else:
            # Fallback to aggregate deductible if no per-coverage info
            deductible = policy.aggregate_deductible
            coverage_limit = Decimal(coverage_analysis.get("total_coverage_available", "0"))
            after_deductible = max(Decimal("0"), claimed_amount - deductible)
            total_payable = min(after_deductible, coverage_limit)

            breakdown_items.append({
                "coverage": "aggregate",
                "item_amount": str(claimed_amount),
                "deductible": str(deductible),
                "after_deductible": str(after_deductible),
                "coverage_limit": str(coverage_limit),
                "payment": str(total_payable),
            })

        # Ensure we don't pay more than claimed
        total_payable = min(total_payable, claimed_amount)

        return {
            "recommended_amount": str(total_payable),
            "claimed_amount": str(claimed_amount),
            "deductible_applied": str(sum(Decimal(b.get("deductible", "0")) for b in breakdown_items)),
            "coverage_limit": str(coverage_analysis.get("total_coverage_available", "0")),
            "reason": DecisionReason.POLICY_COVERAGE_VALID.value,
            "breakdown": {
                "gross_claim": str(claimed_amount),
                "coverage_details": breakdown_items,
                "final_payment": str(total_payable),
            }
        }
    
    def _generate_recommendation(
        self,
        coverage_analysis: Dict[str, Any],
        exclusion_analysis: Dict[str, Any],
        payment_calculation: Dict[str, Any]
    ) -> str:
        """Generate a human-readable recommendation"""
        
        if not coverage_analysis.get("is_covered"):
            return "DENY: Claim is not covered under the policy terms."
        
        if exclusion_analysis.get("exclusions_apply"):
            exclusions = exclusion_analysis.get("applying_exclusions", [])
            return f"DENY: Policy exclusion applies - {exclusions[0].get('exclusion', 'Unknown exclusion')}"
        
        amount = payment_calculation.get("recommended_amount", "0")
        return f"APPROVE: Recommend payment of ${amount}"


class FraudDetectionAgent(BaseAgent):
    """
    Agent responsible for detecting potential fraud indicators.
    Uses pattern matching, anomaly detection, and behavioral analysis.
    """
    
    def __init__(self, llm_client: Any = None, tools: Optional[List[Any]] = None):
        super().__init__(
            name="fraud_detection",
            description="Detects potential fraud indicators using pattern analysis, "
                       "behavioral signals, and cross-referencing with historical data.",
            llm_client=llm_client,
            tools=tools,
        )
        
        self.capabilities = [
            AgentCapability(
                name="analyze_patterns",
                description="Analyze claim patterns for fraud indicators",
                input_schema={"claim": "Claim"},
                output_schema={"risk_score": "float", "indicators": "list"},
            ),
            AgentCapability(
                name="verify_documents",
                description="Verify document authenticity",
                input_schema={"documents": "list[Document]"},
                output_schema={"verification_results": "list"},
            ),
            AgentCapability(
                name="check_history",
                description="Check claimant history for red flags",
                input_schema={"claimant_id": "UUID"},
                output_schema={"history_analysis": "dict"},
            ),
        ]
        
        # Fraud indicator patterns
        self.red_flag_patterns = [
            {
                "name": "recent_policy_change",
                "description": "Coverage increased shortly before claim",
                "severity": "medium",
                "weight": 0.15,
            },
            {
                "name": "quick_claim",
                "description": "Claim filed very soon after policy inception",
                "severity": "medium",
                "weight": 0.12,
            },
            {
                "name": "round_numbers",
                "description": "Claim amounts are suspiciously round",
                "severity": "low",
                "weight": 0.05,
            },
            {
                "name": "excessive_documentation",
                "description": "Unusually detailed documentation provided unsolicited",
                "severity": "low",
                "weight": 0.08,
            },
            {
                "name": "prior_claims_frequency",
                "description": "High frequency of prior claims",
                "severity": "high",
                "weight": 0.20,
            },
            {
                "name": "inconsistent_information",
                "description": "Information doesn't match across documents",
                "severity": "high",
                "weight": 0.25,
            },
        ]
    
    async def execute(self, context: AgentContext, task: AgentTask) -> AgentResult:
        async with self.execution_context(task):
            claim = context.claim
            
            # Analyze various fraud dimensions
            pattern_analysis = await self._analyze_patterns(context, claim)
            document_analysis = await self._analyze_documents(context, claim)
            history_analysis = await self._analyze_history(context, claim)
            behavioral_analysis = await self._analyze_behavior(context, claim)
            
            # Calculate overall fraud risk score
            risk_score, indicators = self._calculate_risk_score(
                pattern_analysis,
                document_analysis,
                history_analysis,
                behavioral_analysis
            )
            
            # Determine if human review is needed
            requires_review = risk_score >= 0.5
            review_reasons = []
            
            if risk_score >= 0.7:
                review_reasons.append(f"High fraud risk score: {risk_score:.2f}")
            
            high_severity_indicators = [i for i in indicators if i.severity == "high"]
            if high_severity_indicators:
                review_reasons.append(
                    f"{len(high_severity_indicators)} high-severity fraud indicator(s)"
                )
            
            # Store in shared memory
            await self.update_shared_memory(context, "fraud_analysis", {
                "risk_score": risk_score,
                "indicators": [i.model_dump() for i in indicators],
                "requires_investigation": requires_review,
            })
            
            return self.create_result(
                task=task,
                success=True,
                output={
                    "risk_score": risk_score,
                    "risk_level": self._get_risk_level(risk_score),
                    "indicators": [i.model_dump() for i in indicators],
                    "pattern_analysis": pattern_analysis,
                    "document_analysis": document_analysis,
                    "history_analysis": history_analysis,
                    "behavioral_analysis": behavioral_analysis,
                },
                reasoning=f"Fraud analysis complete. Risk score: {risk_score:.2f} "
                         f"({self._get_risk_level(risk_score)}). "
                         f"Found {len(indicators)} indicator(s).",
                confidence=0.85,
                requires_human_review=requires_review,
                human_review_reasons=review_reasons,
                next_actions=["final_decision"] if not requires_review else ["human_review"],
            )
    
    async def _analyze_patterns(
        self, context: AgentContext, claim: Claim
    ) -> Dict[str, Any]:
        """Analyze claim for fraud patterns"""

        indicators_found = []

        # Check for round numbers using centralized utility
        amount = claim.total_amount_claimed
        if is_round_number(amount):
            indicators_found.append("round_numbers")

        # Check claim timing
        policy = claim.policy
        if policy:
            days_since_effective = (claim.date_of_loss - policy.effective_date).days
            if days_since_effective < 30:
                indicators_found.append("quick_claim")

        # Check for excessive items
        if len(claim.items) > 20:
            indicators_found.append("excessive_items")

        # Check for items with identical amounts (potential padding)
        if claim.items:
            item_amounts = [item.amount_claimed for item in claim.items]
            duplicate_amounts = len(item_amounts) - len(set(item_amounts))
            if duplicate_amounts >= 3:
                indicators_found.append("duplicate_item_amounts")

        # Check for amount discrepancy (difference between stated and calculated total)
        if getattr(claim, 'amount_discrepancy_flagged', False):
            indicators_found.append("inconsistent_information")

        return {
            "patterns_checked": len(self.red_flag_patterns),
            "patterns_found": indicators_found,
            "pattern_score": len(indicators_found) / max(len(self.red_flag_patterns), 1),
        }
    
    async def _analyze_documents(
        self, context: AgentContext, claim: Claim
    ) -> Dict[str, Any]:
        """Analyze documents for fraud indicators"""
        
        doc_analysis = self.get_from_shared_memory(context, "document_validations", [])
        cross_ref = self.get_from_shared_memory(context, "cross_reference", {})
        
        issues = []
        
        # Check for document inconsistencies
        if not cross_ref.get("consistent", True):
            issues.extend(cross_ref.get("discrepancies", []))
        
        # Check for invalid documents
        invalid_docs = [d for d in doc_analysis if not d.get("is_valid", True)]
        if invalid_docs:
            issues.append({
                "type": "invalid_documents",
                "count": len(invalid_docs),
                "severity": "medium"
            })
        
        return {
            "documents_analyzed": len(claim.documents),
            "issues_found": issues,
            "consistency_score": 1.0 if cross_ref.get("consistent", True) else 0.5,
        }
    
    async def _analyze_history(
        self, context: AgentContext, claim: Claim
    ) -> Dict[str, Any]:
        """Analyze claimant's claim history"""
        
        claimant = claim.claimant
        if not claimant:
            return {"history_available": False}
        
        prior_claims = claimant.previous_claims_count
        risk_score = claimant.risk_score or 0.5
        
        history_flags = []
        if prior_claims > 5:
            history_flags.append("high_claim_frequency")
        if risk_score > 0.7:
            history_flags.append("elevated_risk_profile")
        
        return {
            "history_available": True,
            "prior_claims_count": prior_claims,
            "existing_risk_score": risk_score,
            "history_flags": history_flags,
        }
    
    async def _analyze_behavior(
        self, context: AgentContext, claim: Claim
    ) -> Dict[str, Any]:
        """Analyze behavioral signals"""
        
        # Use LLM for behavioral analysis
        behavior_prompt = f"""Analyze this insurance claim for behavioral fraud indicators:

Claim Description: {claim.description}
Amount Claimed: ${claim.total_amount_claimed}
Days Since Incident: {(date.today() - claim.date_of_loss).days}
Documents Provided: {len(claim.documents)}

Look for:
1. Overly detailed or rehearsed-sounding descriptions
2. Unusual urgency in language
3. Inconsistencies in narrative
4. Patterns common in fraudulent claims

Rate behavioral risk from 0-1 and list any concerns.
Return JSON: {{"risk": float, "concerns": [string]}}
"""
        
        response = await self.think(context, behavior_prompt)
        parsed = parse_json_from_llm_response(response, default={"risk": 0.3, "concerns": []})
        return {
            "behavioral_risk": parsed.get("risk", 0.3),
            "concerns": parsed.get("concerns", []),
        }
    
    def _calculate_risk_score(
        self,
        pattern_analysis: Dict[str, Any],
        document_analysis: Dict[str, Any],
        history_analysis: Dict[str, Any],
        behavioral_analysis: Dict[str, Any]
    ) -> tuple[float, List[FraudIndicator]]:
        """Calculate overall fraud risk score"""
        
        indicators = []
        total_score = 0.0
        
        # Weight different analysis components
        weights = {
            "pattern": 0.25,
            "document": 0.25,
            "history": 0.25,
            "behavioral": 0.25,
        }
        
        # Pattern score
        pattern_score = pattern_analysis.get("pattern_score", 0)
        total_score += pattern_score * weights["pattern"]
        
        for pattern_name in pattern_analysis.get("patterns_found", []):
            pattern_def = next(
                (p for p in self.red_flag_patterns if p["name"] == pattern_name),
                {"name": pattern_name, "description": pattern_name, "severity": "medium"}
            )
            indicators.append(FraudIndicator(
                indicator_type=pattern_name,
                description=pattern_def["description"],
                severity=pattern_def["severity"],
                confidence=0.8,
            ))
        
        # Document score
        doc_score = 1 - document_analysis.get("consistency_score", 1)
        total_score += doc_score * weights["document"]
        
        for issue in document_analysis.get("issues_found", []):
            indicators.append(FraudIndicator(
                indicator_type=issue.get("type", "document_issue"),
                description=str(issue),
                severity=issue.get("severity", "medium"),
                confidence=0.75,
            ))
        
        # History score - bounded calculation
        if history_analysis.get("history_available"):
            history_flags = history_analysis.get("history_flags", [])
            prior_claims = history_analysis.get("prior_claims_count", 0)
            existing_risk = history_analysis.get("existing_risk_score", 0.5)

            # Calculate history score based on multiple factors
            # - Each flag contributes up to 0.15 (capped at 3 flags = 0.45)
            # - Prior claims > 3 adds 0.1 per claim (capped at 0.3)
            # - Existing risk score contributes directly (weighted at 0.25)
            flag_contribution = min(len(history_flags) * 0.15, 0.45)
            claims_contribution = min(max(0, prior_claims - 3) * 0.1, 0.3)
            risk_contribution = existing_risk * 0.25

            history_score = min(flag_contribution + claims_contribution + risk_contribution, 1.0)
            total_score += history_score * weights["history"]

            for flag in history_flags:
                indicators.append(FraudIndicator(
                    indicator_type=flag,
                    description=f"Historical flag: {flag}",
                    severity="medium",
                    confidence=0.85,
                ))
        
        # Behavioral score
        behavioral_risk = behavioral_analysis.get("behavioral_risk", 0.3)
        total_score += behavioral_risk * weights["behavioral"]
        
        for concern in behavioral_analysis.get("concerns", []):
            indicators.append(FraudIndicator(
                indicator_type="behavioral",
                description=concern,
                severity="low",
                confidence=0.6,
            ))
        
        return min(total_score, 1.0), indicators
    
    def _get_risk_level(self, score: float) -> str:
        """Convert numeric score to risk level"""
        if score < 0.3:
            return "low"
        elif score < 0.5:
            return "medium"
        elif score < 0.7:
            return "high"
        return "critical"


class DecisionAgent(BaseAgent):
    """
    Agent responsible for making final adjudication decisions.
    Synthesizes all analysis and produces the final ruling.
    """
    
    def __init__(self, llm_client: Any = None, tools: Optional[List[Any]] = None):
        super().__init__(
            name="decision_maker",
            description="Makes final adjudication decisions by synthesizing all agent "
                       "analyses and producing comprehensive rulings with explanations.",
            llm_client=llm_client,
            tools=tools,
        )
        
        self.capabilities = [
            AgentCapability(
                name="synthesize_analysis",
                description="Combine all agent analyses into coherent decision",
                input_schema={"context": "AgentContext"},
                output_schema={"decision": "AdjudicationDecision"},
            ),
            AgentCapability(
                name="generate_explanation",
                description="Generate human-readable decision explanation",
                input_schema={"decision": "AdjudicationDecision"},
                output_schema={"explanation": "string"},
            ),
        ]
    
    async def execute(self, context: AgentContext, task: AgentTask) -> AgentResult:
        async with self.execution_context(task):
            claim = context.claim
            
            # Gather all analyses from shared memory
            policy_analysis = self.get_from_shared_memory(context, "policy_analysis", {})
            fraud_analysis = self.get_from_shared_memory(context, "fraud_analysis", {})
            document_data = self.get_from_shared_memory(context, "extracted_documents", {})

            # Calculate preliminary confidence to check floor
            coverage = policy_analysis.get("coverage", {})
            exclusions = policy_analysis.get("exclusions", {})
            preliminary_confidence = self._calculate_confidence(
                coverage.get("confidence", 0.5),
                exclusions.get("confidence", 0.5),
                1 - fraud_analysis.get("risk_score", 0.5)
            )

            # Check if we should defer to human
            should_defer, defer_reasons = self._should_defer_to_human(
                policy_analysis, fraud_analysis, preliminary_confidence
            )
            if should_defer:
                return await self._create_human_review_result(
                    context, task, policy_analysis, fraud_analysis, defer_reasons
                )
            
            # Make decision
            decision = await self._make_decision(
                context, claim, policy_analysis, fraud_analysis, document_data
            )
            
            # Generate detailed explanation
            explanation = await self._generate_explanation(
                context, claim, decision, policy_analysis, fraud_analysis
            )
            decision.detailed_explanation = explanation
            
            # Set appeal deadline (30 days from decision)
            decision.appeal_deadline = date.today() + timedelta(days=30)
            
            return self.create_result(
                task=task,
                success=True,
                output={
                    "decision": decision.model_dump(),
                    "summary": self._generate_summary(decision),
                },
                reasoning=f"Decision: {decision.decision.upper()}. "
                         f"Approved: ${decision.approved_amount}, "
                         f"Denied: ${decision.denied_amount}. "
                         f"Confidence: {decision.confidence_score:.2%}",
                confidence=decision.confidence_score,
                requires_human_review=decision.requires_human_review,
                human_review_reasons=decision.human_review_reasons,
            )
    
    def _should_defer_to_human(
        self,
        policy_analysis: Dict[str, Any],
        fraud_analysis: Dict[str, Any],
        preliminary_confidence: Optional[float] = None
    ) -> tuple[bool, List[str]]:
        """Determine if case should go to human review"""

        reasons = []

        # High fraud risk
        if fraud_analysis.get("risk_score", 0) >= 0.7:
            reasons.append(f"High fraud risk score: {fraud_analysis.get('risk_score', 0):.2f}")

        # Coverage ambiguity
        coverage = policy_analysis.get("coverage", {})
        if coverage.get("confidence", 1.0) < 0.6:
            reasons.append(f"Low coverage confidence: {coverage.get('confidence', 0):.2f}")

        # Check confidence floor - if preliminary confidence is too low, always defer
        if preliminary_confidence is not None:
            if preliminary_confidence < settings.agent.min_confidence_floor:
                reasons.append(
                    f"Confidence {preliminary_confidence:.2%} below minimum floor "
                    f"({settings.agent.min_confidence_floor:.2%})"
                )

        return len(reasons) > 0, reasons
    
    async def _create_human_review_result(
        self,
        context: AgentContext,
        task: AgentTask,
        policy_analysis: Dict[str, Any],
        fraud_analysis: Dict[str, Any],
        reasons: Optional[List[str]] = None
    ) -> AgentResult:
        """Create result for human review cases"""

        if reasons is None:
            reasons = []

            if fraud_analysis.get("risk_score", 0) >= 0.7:
                reasons.append(
                    f"High fraud risk score: {fraud_analysis['risk_score']:.2f}"
                )

            coverage = policy_analysis.get("coverage", {})
            if coverage.get("confidence", 1.0) < 0.6:
                reasons.append(
                    f"Low coverage confidence: {coverage.get('confidence', 0):.2f}"
                )

        return self.create_result(
            task=task,
            success=True,
            output={
                "decision": "pending_human_review",
                "preliminary_analysis": {
                    "policy": policy_analysis,
                    "fraud": fraud_analysis,
                },
            },
            reasoning="Case requires human review due to: " + "; ".join(reasons),
            confidence=0.5,
            requires_human_review=True,
            human_review_reasons=reasons,
        )
    
    async def _make_decision(
        self,
        context: AgentContext,
        claim: Claim,
        policy_analysis: Dict[str, Any],
        fraud_analysis: Dict[str, Any],
        document_data: Dict[str, Any]
    ) -> AdjudicationDecision:
        """Make the final adjudication decision"""

        payment_calc = policy_analysis.get("payment", {})
        coverage = policy_analysis.get("coverage", {})
        exclusions = policy_analysis.get("exclusions", {})
        fraud_risk_score = fraud_analysis.get("risk_score", 0)
        fraud_indicators = fraud_analysis.get("indicators", [])

        # Check for high-severity fraud indicators
        high_severity_fraud = [
            ind for ind in fraud_indicators
            if ind.get("severity") == "high"
        ]

        # Determine decision type - check fraud FIRST before approving
        if fraud_risk_score >= 0.7:
            # Critical fraud risk - deny claim
            decision_type = "denied"
            approved_amount = Decimal("0")
            denied_amount = claim.total_amount_claimed
            reasons = [DecisionReason.FRAUD_INDICATORS]
        elif fraud_risk_score >= 0.5 and high_severity_fraud:
            # High fraud risk with indicators - deny claim
            decision_type = "denied"
            approved_amount = Decimal("0")
            denied_amount = claim.total_amount_claimed
            reasons = [DecisionReason.FRAUD_INDICATORS]
        elif not coverage.get("is_covered"):
            decision_type = "denied"
            approved_amount = Decimal("0")
            denied_amount = claim.total_amount_claimed
            reasons = [DecisionReason.COVERAGE_EXCLUDED]
        elif exclusions.get("exclusions_apply"):
            decision_type = "denied"
            approved_amount = Decimal("0")
            denied_amount = claim.total_amount_claimed
            reasons = [DecisionReason.COVERAGE_EXCLUDED]
        else:
            recommended = Decimal(payment_calc.get("recommended_amount", "0"))

            # If fraud risk is medium (0.3-0.5), reduce approved amount by risk factor
            if fraud_risk_score >= 0.3:
                fraud_reduction = Decimal(str(1 - fraud_risk_score))
                recommended = recommended * fraud_reduction

            if recommended >= claim.total_amount_claimed:
                decision_type = "approved"
                reasons = [
                    DecisionReason.POLICY_COVERAGE_VALID,
                    DecisionReason.DOCUMENTATION_COMPLETE,
                ]
            elif recommended > 0:
                decision_type = "partially_approved"
                reasons = [
                    DecisionReason.POLICY_COVERAGE_VALID,
                    DecisionReason.DEDUCTIBLE_NOT_MET if payment_calc.get("deductible_applied") else DecisionReason.EXCEEDS_COVERAGE_LIMIT,
                ]
            else:
                decision_type = "denied"
                reasons = [DecisionReason.DEDUCTIBLE_NOT_MET]

            approved_amount = recommended
            denied_amount = claim.total_amount_claimed - recommended
        
        # Calculate confidence
        confidence = self._calculate_confidence(
            coverage.get("confidence", 0.5),
            exclusions.get("confidence", 0.5),
            1 - fraud_analysis.get("risk_score", 0.5)
        )
        
        # Create fraud indicators
        fraud_indicators = [
            FraudIndicator(**ind)
            for ind in fraud_analysis.get("indicators", [])
        ]
        
        return AdjudicationDecision(
            decision=decision_type,
            confidence_score=confidence,
            approved_amount=approved_amount,
            denied_amount=denied_amount,
            reasons=reasons,
            detailed_explanation="",  # Will be filled by _generate_explanation
            coverage_analysis=coverage,
            fraud_indicators=fraud_indicators,
            requires_human_review=confidence < settings.agent.human_review_threshold,
            human_review_reasons=[
                f"Confidence {confidence:.2%} below threshold"
            ] if confidence < settings.agent.human_review_threshold else [],
        )
    
    def _calculate_confidence(
        self,
        coverage_confidence: float,
        exclusion_confidence: float,
        fraud_confidence: float
    ) -> float:
        """Calculate overall decision confidence"""
        # Weighted average with fraud having highest weight
        weights = {"coverage": 0.35, "exclusion": 0.25, "fraud": 0.40}
        
        return (
            coverage_confidence * weights["coverage"] +
            exclusion_confidence * weights["exclusion"] +
            fraud_confidence * weights["fraud"]
        )
    
    async def _generate_explanation(
        self,
        context: AgentContext,
        claim: Claim,
        decision: AdjudicationDecision,
        policy_analysis: Dict[str, Any],
        fraud_analysis: Dict[str, Any]
    ) -> str:
        """Generate human-readable explanation"""
        
        explanation_prompt = f"""Generate a clear, professional explanation for this insurance claim decision.

Decision: {decision.decision.upper()}
Approved Amount: ${decision.approved_amount}
Denied Amount: ${decision.denied_amount}

Reasons: {[r.value for r in decision.reasons]}

Policy Analysis:
- Coverage applicable: {policy_analysis.get('coverage', {}).get('is_covered')}
- Exclusions apply: {policy_analysis.get('exclusions', {}).get('exclusions_apply')}

Fraud Analysis:
- Risk score: {fraud_analysis.get('risk_score', 0):.2f}
- Indicators found: {len(fraud_analysis.get('indicators', []))}

Write a 2-3 paragraph explanation that:
1. States the decision clearly
2. Explains the reasoning based on policy terms
3. Details any deductions or limits applied
4. Provides information about the appeals process if denied
"""
        
        explanation = await self.think(context, explanation_prompt)
        return explanation
    
    def _generate_summary(self, decision: AdjudicationDecision) -> str:
        """Generate a one-line summary"""
        return (
            f"{decision.decision.upper()}: "
            f"${decision.approved_amount} approved, "
            f"${decision.denied_amount} denied. "
            f"Confidence: {decision.confidence_score:.0%}"
        )
