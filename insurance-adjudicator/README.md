# Insurance AI Adjudicator

A production-ready, agentic AI system for automated insurance claim adjudication. Built with scalability, reliability, and compliance in mind.

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Insurance AI Adjudicator                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌─────────────────────────────────────────────────┐    │
│  │   FastAPI   │    │              Agent Orchestrator                  │    │
│  │   REST API  │───▶│  ┌─────────────────────────────────────────┐    │    │
│  └─────────────┘    │  │           Workflow Engine                │    │    │
│                     │  │  • Sequential/Parallel Execution         │    │    │
│                     │  │  • State Management                      │    │    │
│                     │  │  • Error Recovery                        │    │    │
│                     │  └─────────────────────────────────────────┘    │    │
│                     │                      │                          │    │
│                     │     ┌────────────────┼────────────────┐         │    │
│                     │     ▼                ▼                ▼         │    │
│                     │  ┌──────┐      ┌──────────┐     ┌─────────┐    │    │
│                     │  │ Doc  │      │ Policy   │     │ Fraud   │    │    │
│                     │  │ Ext. │      │ Analysis │     │ Detect. │    │    │
│                     │  └──────┘      └──────────┘     └─────────┘    │    │
│                     │                      │                          │    │
│                     │                      ▼                          │    │
│                     │              ┌──────────────┐                   │    │
│                     │              │   Decision   │                   │    │
│                     │              │    Agent     │                   │    │
│                     │              └──────────────┘                   │    │
│                     └─────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌────────────┐  │
│  │ PostgreSQL  │    │    Redis    │    │   Jaeger    │    │ Prometheus │  │
│  │  Database   │    │   Cache     │    │  Tracing    │    │  Metrics   │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 🚀 Features

### Agentic AI Capabilities
- **Document Extraction Agent**: OCR and NLP-powered document analysis
- **Policy Analysis Agent**: Automated coverage determination and limit calculations
- **Fraud Detection Agent**: ML-based fraud pattern detection with risk scoring
- **Decision Agent**: Final adjudication with detailed explanations

### Production-Ready Infrastructure
- **Scalable Architecture**: Horizontal scaling with Kubernetes support
- **High Availability**: Redis caching, connection pooling, health checks
- **Observability**: OpenTelemetry tracing, Prometheus metrics, structured logging
- **Security**: API authentication, rate limiting, input validation

### Workflow Management
- **Parallel Execution**: Process independent agents concurrently
- **State Management**: Shared context across agents
- **Error Recovery**: Automatic retries with exponential backoff
- **Human Escalation**: Intelligent routing for edge cases

## 📦 Installation

### Prerequisites
- Python 3.11+
- Docker & Docker Compose
- PostgreSQL 16+
- Redis 7+

### Quick Start

```bash
# Clone the repository
git clone https://github.com/your-org/insurance-adjudicator.git
cd insurance-adjudicator

# Set up environment variables
cp .env.example .env
# Edit .env with your LLM API key

# Start with Docker Compose
docker-compose up -d

# Or install locally
pip install -e ".[dev]"
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | LLM provider (anthropic, openai) | anthropic |
| `LLM_API_KEY` | API key for LLM provider | - |
| `LLM_MODEL` | Model to use | claude-sonnet-4-20250514 |
| `DB_HOST` | PostgreSQL host | localhost |
| `DB_PORT` | PostgreSQL port | 5432 |
| `REDIS_HOST` | Redis host | localhost |
| `AGENT_CONFIDENCE_THRESHOLD` | Auto-approval threshold | 0.85 |
| `AGENT_HUMAN_REVIEW_THRESHOLD` | Human review threshold | 0.70 |

## 🔧 Usage

### REST API

#### Submit a Claim

```bash
curl -X POST http://localhost:8000/api/v1/claims \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "claim_type": "auto",
    "policy_number": "POL-2024-001",
    "date_of_loss": "2024-06-15",
    "description": "Vehicle collision at intersection",
    "total_amount_claimed": 15000.00,
    "items": [
      {
        "description": "Front bumper repair",
        "category": "repair",
        "amount_claimed": 15000.00,
        "date_of_loss": "2024-06-15"
      }
    ]
  }'
```

#### Get Claim Status

```bash
curl http://localhost:8000/api/v1/claims/{claim_id} \
  -H "Authorization: Bearer your-api-key"
```

#### Trigger Manual Adjudication

```bash
curl -X POST http://localhost:8000/api/v1/claims/{claim_id}/adjudicate \
  -H "Authorization: Bearer your-api-key"
```

### Python SDK

```python
from src.agents.orchestrator import AdaptiveOrchestrator
from src.models.claim import Claim, ClaimType
from src.services.llm_client import create_llm_client

# Initialize
llm_client = create_llm_client(provider="anthropic")
orchestrator = AdaptiveOrchestrator(llm_client=llm_client)

# Process a claim
claim = Claim(
    claim_number="CLM-001",
    claim_type=ClaimType.AUTO,
    # ... other fields
)

result = await orchestrator.process_claim(claim)

print(f"Decision: {result.decision.decision}")
print(f"Approved: ${result.decision.approved_amount}")
print(f"Confidence: {result.decision.confidence_score:.2%}")
```

## 🏛️ System Components

### Agents

#### Document Extraction Agent
- Extracts text from PDFs, images, and scanned documents
- Identifies key information (dates, amounts, parties)
- Validates document authenticity
- Cross-references multiple documents for consistency

#### Policy Analysis Agent
- Verifies policy validity and coverage period
- Determines applicable coverages for claim type
- Calculates deductibles and coverage limits
- Identifies policy exclusions

#### Fraud Detection Agent
- Analyzes claim patterns for anomalies
- Checks claimant history and risk profile
- Detects document inconsistencies
- Assigns fraud risk scores

#### Decision Agent
- Synthesizes all agent analyses
- Makes final adjudication decision
- Generates human-readable explanations
- Determines if human review is needed

### Workflow Engine

```python
# Default workflow configuration
WorkflowConfig(
    steps=[
        WorkflowStep.INITIALIZE,
        WorkflowStep.DOCUMENT_EXTRACTION,
        WorkflowStep.POLICY_ANALYSIS,      # ──┐
        WorkflowStep.FRAUD_DETECTION,      # ──┤ Parallel
        WorkflowStep.FINAL_DECISION,
        WorkflowStep.COMPLETE,
    ],
    parallel_steps=[
        [WorkflowStep.POLICY_ANALYSIS, WorkflowStep.FRAUD_DETECTION]
    ],
)
```

## 📊 API Reference

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/claims` | Submit a new claim |
| GET | `/api/v1/claims` | List claims with filtering |
| GET | `/api/v1/claims/{id}` | Get claim details |
| POST | `/api/v1/claims/{id}/adjudicate` | Trigger adjudication |
| POST | `/api/v1/claims/batch` | Batch submission |
| GET | `/api/v1/metrics` | System metrics |
| GET | `/health` | Health check |
| GET | `/ready` | Readiness check |

### Response Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request |
| 401 | Unauthorized |
| 404 | Not Found |
| 500 | Internal Server Error |

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_adjudicator.py -v

# Run async tests
pytest -x -v --asyncio-mode=auto
```

## 📈 Monitoring

### Metrics (Prometheus)
- `claims_processed_total` - Total claims processed
- `claim_processing_duration_seconds` - Processing time histogram
- `agent_execution_duration_seconds` - Per-agent execution time
- `human_review_required_total` - Claims requiring human review

### Tracing (Jaeger)
- End-to-end request tracing
- Per-agent span tracking
- Error highlighting

### Dashboards (Grafana)
Access at `http://localhost:3000` (default: admin/admin)

## 🔐 Security

### Authentication
- Bearer token authentication
- API key validation
- Role-based access control (RBAC)

### Data Protection
- Input validation with Pydantic
- SQL injection prevention
- Encrypted sensitive data

### Compliance
- Audit logging
- Decision trail preservation
- GDPR data handling

## 🚢 Deployment

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: insurance-adjudicator
spec:
  replicas: 3
  selector:
    matchLabels:
      app: insurance-adjudicator
  template:
    spec:
      containers:
      - name: api
        image: insurance-adjudicator:latest
        ports:
        - containerPort: 8000
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "2000m"
```

### Helm Chart
```bash
helm install adjudicator ./helm/insurance-adjudicator \
  --set llm.apiKey=$LLM_API_KEY \
  --set replicas=3
```

## 📁 Project Structure

```
insurance-adjudicator/
├── src/
│   ├── agents/
│   │   ├── base.py           # Base agent class
│   │   ├── specialized.py    # Specialized agents
│   │   └── orchestrator.py   # Workflow orchestration
│   ├── api/
│   │   └── routes.py         # FastAPI endpoints
│   ├── config/
│   │   └── settings.py       # Configuration
│   ├── models/
│   │   └── claim.py          # Data models
│   └── services/
│       └── llm_client.py     # LLM abstraction
├── tests/
│   └── test_adjudicator.py   # Test suite
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🆘 Support

- Documentation: [docs.example.com](https://docs.example.com)
- Issues: [GitHub Issues](https://github.com/your-org/insurance-adjudicator/issues)
- Email: support@example.com
