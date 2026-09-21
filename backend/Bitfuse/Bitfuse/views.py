from django.http import JsonResponse


def health_check(request):
    """Lightweight health check endpoint for container / orchestrator readiness probes."""
    return JsonResponse({"status": "healthy"}, status=200)
