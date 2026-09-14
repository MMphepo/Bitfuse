import logging
from django.db import DatabaseError, OperationalError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """Custom exception handler for Django REST Framework.

    Handles standard DRF exceptions, database connection errors, and
    unexpected server exceptions so that DRF always returns a structured JSON
    Response. This ensures django-cors-headers middleware properly attaches CORS
    headers (e.g. Access-Control-Allow-Origin) even on 500/503 errors.
    """
    response = exception_handler(exc, context)

    if response is not None:
        return response

    if isinstance(exc, (OperationalError, DatabaseError)):
        logger.error("Database connection error: %s", exc, exc_info=True)
        return Response(
            {"detail": "Database service temporarily unavailable. Please try again shortly."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    logger.error("Unhandled server exception: %s", exc, exc_info=True)
    return Response(
        {"detail": "An internal server error occurred. Please try again shortly."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
