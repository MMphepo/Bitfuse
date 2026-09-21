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

            error_code = getattr(exc, "error_code", None)
            if isinstance(raw_data, dict):
                if "code" in raw_data:
                    error_code = raw_data["code"]
                msg = str(raw_data.get("detail", raw_data.get("message", "")))
            elif isinstance(raw_data, str):
                msg = raw_data
            else:
                msg = ""

            if response.status_code == status.HTTP_401_UNAUTHORIZED:
                if not error_code:
                    detail_code = ""
                    if isinstance(raw_data, dict) and "code" in raw_data:
                        detail_code = str(raw_data["code"])
                    elif isinstance(raw_data, dict) and "detail" in raw_data and hasattr(raw_data["detail"], "code"):
                        detail_code = str(raw_data["detail"].code)

                    if detail_code and detail_code.isupper():
                        error_code = detail_code
                    else:
                        msg_lower = msg.lower()
                        if "invalid email or password" in msg_lower or "invalid credentials" in msg_lower:
                            error_code = "INVALID_CREDENTIALS"
                        elif "session" in msg_lower and "expired" in msg_lower:
                            error_code = "SESSION_EXPIRED"
                        elif "session" in msg_lower and "revoked" in msg_lower:
                            error_code = "SESSION_REVOKED"
                        elif "expired" in msg_lower:
                            error_code = "TOKEN_EXPIRED"
                        elif "token" in msg_lower or detail_code == "token_not_valid":
                            error_code = "TOKEN_INVALID"
                        else:
                            error_code = "AUTHENTICATION_REQUIRED"

                if not msg:
                    if error_code == "INVALID_CREDENTIALS":
                        msg = "Invalid email or password."
                    elif error_code == "TOKEN_EXPIRED":
                        msg = "Your access token has expired."
                    elif error_code == "SESSION_EXPIRED":
                        msg = "Your session has expired due to inactivity."
                    elif error_code == "SESSION_REVOKED":
                        msg = "Your session has been revoked."
                    elif error_code == "TOKEN_INVALID":
                        msg = "Token is invalid or expired."
                    else:
                        msg = "Authentication credentials were not provided."

                response.data = {
                    "success": False,
                    "message": msg,
                    "code": error_code,
                }
                return response

            if response.status_code == status.HTTP_403_FORBIDDEN:
                if not error_code:
                    error_code = "PERMISSION_DENIED"
                if not msg:
                    msg = "You do not have permission to perform this action."

                response.data = {
                    "success": False,
                    "message": msg,
                    "code": error_code,
                }
                return response

        if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            response.data = {
                "success": False,
                "message": "Too many requests. Please slow down and try again later.",
                "code": "TOO_MANY_REQUESTS",
            }
            return response

        return response

    if isinstance(exc, (OperationalError, DatabaseError)):
        logger.error("Database connection error: %s", exc, exc_info=True)
        return Response(
            {
                "success": False,
                "message": "Database service temporarily unavailable. Please try again shortly.",
                "code": "SERVICE_UNAVAILABLE",
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    logger.error("Unhandled server exception: %s", exc, exc_info=True)
    return Response(
        {
            "success": False,
            "message": "An unexpected server error occurred. Please try again later.",
            "code": "SERVER_ERROR",
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
