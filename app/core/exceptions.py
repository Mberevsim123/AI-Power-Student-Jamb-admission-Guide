"""
app/core/exceptions.py
Domain exceptions + FastAPI exception handlers.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class JAMBGuideException(Exception):
    """Base for all domain errors."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class EligibilityError(JAMBGuideException):
    pass


class ConversationNotFoundError(JAMBGuideException):
    def __init__(self, conversation_id: int):
        super().__init__(f"Conversation {conversation_id} not found", status_code=404)


class UniversityNotFoundError(JAMBGuideException):
    def __init__(self, name: str):
        super().__init__(f"University '{name}' not found", status_code=404)


class AdmissionCycleNotFoundError(JAMBGuideException):
    def __init__(self):
        super().__init__("No active admission cycle found", status_code=404)


class LLMError(JAMBGuideException):
    def __init__(self, detail: str):
        super().__init__(f"AI service error: {detail}", status_code=503)


class RAGError(JAMBGuideException):
    def __init__(self, detail: str):
        super().__init__(f"Knowledge retrieval error: {detail}", status_code=503)


class LLMUnavailableError(JAMBGuideException):
    def __init__(self, message="AI service is unavailable. Please try again later."):
        super().__init__(message, status_code=503)


class LLMQuotaError(JAMBGuideException):
    def __init__(
        self, message="AI provider limit reached. Please try again later.", retry_after=None
    ):
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Register handlers on the FastAPI app
# ---------------------------------------------------------------------------


def register_exception_handlers(app: FastAPI) -> None:

    @app.exception_handler(JAMBGuideException)
    async def domain_error_handler(request: Request, exc: JAMBGuideException):
        retry_after = getattr(exc, "retry_after", None)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "type": type(exc).__name__},
            headers={"Retry-After": str(retry_after)} if retry_after else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        errors = []
        for error in exc.errors():
            errors.append(
                {
                    "field": " → ".join(str(loc) for loc in error["loc"]),
                    "message": error["msg"],
                }
            )
        return JSONResponse(
            status_code=422,
            content={"detail": "Validation error", "errors": errors},
        )

    @app.exception_handler(IntegrityError)
    async def db_integrity_handler(request: Request, exc: IntegrityError):
        logger.error("DB integrity error: %s", exc)
        return JSONResponse(
            status_code=409,
            content={"detail": "Data conflict — record may already exist"},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )
