from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from .models import OperationStatus
from .readiness import launch_requirements


@staff_member_required
def launch_status(request):
    return render(request, 'menu/launch_status.html', {'checks':launch_requirements(),
        'operations':OperationStatus.objects.all()})


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        return JsonResponse({'status':'unavailable'}, status=503)
    return JsonResponse({'status':'ok'})
