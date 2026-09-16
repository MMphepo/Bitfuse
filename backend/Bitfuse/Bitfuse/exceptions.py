import logging
from django.db import DatabaseError, OperationalError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def _format_errors(data):
    if isinstance(data, dict):
        formatted = {}
        for key, val in data.items():
            if isinstance(val, list):
                formatted[key] = [str(item) for item in val]
            elif isinstance(val, dict):
                formatted[key] = _format_errors(val)
            else:
                formatted[key] = [str(val)]
        return formatted
    elif isinstance(data, list):
        return {"non_field_errors": [str(item) for item in data]}
    return {"non_field_errors": [str(data)]}


def custom_exception_handler(exc, context):
    """Custom exception handler for Django REST Framework.

    Formats DRF exceptions into standard predictable API responses:
    HTTP 400:
    {
      "success": false,
      "message": "Please correct the highlighted fields.",
      "errors": { ... }
    }
    HTTP 401/403/429:
    {
      "success": false,
      "message": "..."
    }
    HTTP 500/503:
    {
      "success": false,
      "message": "..."
    }
    """
    response = exception_handler(exc, context)

    if response is not None:
        if response.status_code == status.HTTP_400_BAD_REQUEST:
            raw_data = response.data
            if isinstance(raw_data, dict) and "success" in raw_data:
                return response

            message = "Please correct the highlighted fields."
            if isinstance(raw_data, dict) and "message" in raw_data and isinstance(raw_data["message"], str):
                message = raw_data["message"]
                raw_data = raw_data.get("errors", raw_data)

            formatted_errors = _format_errors(raw_data)
            response.data = {
                "success": False,
                "message": message,
                "errors": formatted_errors,
            }
            return response

        if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
            raw_data = response.data
            if isinstance(raw_data, dict) and "success" in raw_data:
                return response

            msg = "Authentication failed."
            if response.status_code == status.HTTP_401_UNAUTHORIZED:
                msg = "Invalid email or password."
            elif isinstance(raw_data, dict) and "detail" in raw_data:
                msg = str(raw_data["detail"])
            elif isinstance(raw_data, str):
                msg = raw_data

            response.data = {
                "success": False,
                "message": msg,
            }
            return response

        if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            response.data = {
                "success": False,
                "message": "Too many requests. Please slow down and try again later.",
            }
            return response

        return response

    if isinstance(exc, (OperationalError, DatabaseError)):
        logger.error("Database connection error: %s", exc, exc_info=True)
        return Response(
            {
                "success": False,
                "message": "Database service temporarily unavailable. Please try again shortly.",
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    logger.error("Unhandled server exception: %s", exc, exc_info=True)
    return Response(
        {
            "success": False,
            "message": "Something went wrong while creating your account. Please try again.",
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
