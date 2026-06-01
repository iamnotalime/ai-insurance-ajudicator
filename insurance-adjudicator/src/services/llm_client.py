"""
LLM Client Abstraction Layer
Provides a unified interface for different LLM providers
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, AsyncGenerator
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field

from ..config.settings import settings


logger = logging.getLogger(__name__)


class Message(BaseModel):
    """A message in a conversation"""
    role: str  # system, user, assistant
    content: str


class LLMResponse(BaseModel):
    """Response from an LLM"""
    content: str
    model: str
    usage: Dict[str, int] = Field(default_factory=dict)
    finish_reason: Optional[str] = None
    latency_ms: float = 0.0


class LLMClient(ABC):
    """Abstract base class for LLM clients"""
    
    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> str:
        """Generate a response from the LLM"""
        pass
    
    @abstractmethod
    async def generate_with_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate a response with tool use capability"""
        pass
    
    @abstractmethod
    async def stream(
        self,
        prompt: str,
        system: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """Stream a response from the LLM"""
        pass


class AnthropicClient(LLMClient):
    """Client for Anthropic's Claude API"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: str = "https://api.anthropic.com",
    ):
        self.api_key = api_key or settings.llm.api_key
        self.model = model or settings.llm.model
        self.base_url = base_url
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                timeout=settings.llm.timeout,
            )
        return self._client
    
    async def close(self) -> None:
        """Close the HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> str:
        """Generate a response from Claude"""
        
        client = await self._get_client()
        start_time = datetime.now(timezone.utc)
        
        payload = {
            "model": self.model,
            "max_tokens": max_tokens or settings.llm.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        
        if system:
            payload["system"] = system
        
        if temperature is not None:
            payload["temperature"] = temperature
        else:
            payload["temperature"] = settings.llm.temperature
        
        try:
            response = await client.post("/v1/messages", json=payload)
            response.raise_for_status()
            
            data = response.json()
            content = data.get("content", [{}])[0].get("text", "")
            
            latency = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            logger.debug(f"LLM response in {latency:.0f}ms")
            
            return content
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Anthropic API error: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            raise
    
    async def generate_with_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate a response with tool use"""
        
        client = await self._get_client()
        
        payload = {
            "model": self.model,
            "max_tokens": kwargs.get("max_tokens", settings.llm.max_tokens),
            "messages": [{"role": "user", "content": prompt}],
            "tools": tools,
        }
        
        if system:
            payload["system"] = system
        
        try:
            response = await client.post("/v1/messages", json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Tool-enabled generation failed: {e}")
            raise
    
    async def stream(
        self,
        prompt: str,
        system: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """Stream a response from Claude"""
        
        client = await self._get_client()
        
        payload = {
            "model": self.model,
            "max_tokens": kwargs.get("max_tokens", settings.llm.max_tokens),
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        
        if system:
            payload["system"] = system
        
        async with client.stream("POST", "/v1/messages", json=payload) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    try:
                        import json
                        data = json.loads(line[6:])
                        if data.get("type") == "content_block_delta":
                            text = data.get("delta", {}).get("text", "")
                            if text:
                                yield text
                    except json.JSONDecodeError:
                        continue


class OpenAIClient(LLMClient):
    """Client for OpenAI's GPT API"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4-turbo-preview",
        base_url: str = "https://api.openai.com",
    ):
        self.api_key = api_key or settings.llm.api_key
        self.model = model
        self.base_url = base_url
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                },
                timeout=settings.llm.timeout,
            )
        return self._client
    
    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> str:
        client = await self._get_client()
        
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or settings.llm.max_tokens,
            "temperature": temperature if temperature is not None else settings.llm.temperature,
        }
        
        try:
            response = await client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()
            
            data = response.json()
            return data["choices"][0]["message"]["content"]
            
        except Exception as e:
            logger.error(f"OpenAI generation failed: {e}")
            raise
    
    async def generate_with_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        client = await self._get_client()
        
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        # Convert to OpenAI tool format
        openai_tools = [
            {
                "type": "function",
                "function": tool
            }
            for tool in tools
        ]
        
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": openai_tools,
        }
        
        try:
            response = await client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Tool-enabled generation failed: {e}")
            raise
    
    async def stream(
        self,
        prompt: str,
        system: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        client = await self._get_client()
        
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        
        async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        import json
                        data = json.loads(line[6:])
                        content = data["choices"][0]["delta"].get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError):
                        continue


class MockLLMClient(LLMClient):
    """Mock LLM client for testing"""
    
    def __init__(self, responses: Optional[Dict[str, str]] = None):
        self.responses = responses or {}
        self.call_history: List[Dict[str, Any]] = []
    
    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        **kwargs
    ) -> str:
        self.call_history.append({
            "prompt": prompt,
            "system": system,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        
        # Check for matching response
        for key, response in self.responses.items():
            if key.lower() in prompt.lower():
                return response
        
        return '{"confidence": 0.8, "result": "mock_response"}'
    
    async def generate_with_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        self.call_history.append({
            "prompt": prompt,
            "tools": tools,
            "system": system,
        })
        
        return {
            "content": [{"type": "text", "text": "Mock tool response"}],
            "model": "mock",
        }
    
    async def stream(
        self,
        prompt: str,
        system: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        response = await self.generate(prompt, system)
        for word in response.split():
            yield word + " "
            await asyncio.sleep(0.01)


def create_llm_client(
    provider: Optional[str] = None,
    **kwargs
) -> LLMClient:
    """Factory function to create LLM clients"""
    
    provider = provider or settings.llm.provider
    
    if provider == "anthropic":
        return AnthropicClient(**kwargs)
    elif provider == "openai":
        return OpenAIClient(**kwargs)
    elif provider == "mock":
        return MockLLMClient(**kwargs)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
