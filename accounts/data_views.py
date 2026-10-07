from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from orders.models import Notification, Order, OrderItem, WhatsAppMessage, PaymentAttempt, PixPayment
from .models import DataRequest


@login_required
def export_data(request):
    user = request.user
    profile = user.profile
    payload = {'conta': {'nome':user.get_full_name(), 'email':user.email, 'usuario':user.username,
        'telefone':profile.phone, 'cpf':profile.cpf},
        'endereco': {'rua':profile.address_street, 'numero':profile.address_number,
            'complemento':profile.address_complement, 'cidade':str(profile.city or ''),
            'bairro':str(profile.neighborhood or '')},
        'pedidos': [{'numero':order.order_number, 'data':order.created_at.isoformat(),
            'status':order.status, 'pagamento':order.payment_status, 'total':str(order.total),
            'endereco':order.address, 'telefone':order.phone, 'cpf':order.customer_cpf,
            'whatsapp':order.whatsapp_opt_in,
            'itens':[{'nome':item.item_name, 'quantidade':item.quantity, 'total':str(item.line_total),
                'observacoes':item.notes, 'complementos':list(item.options.values('group_name','choice_name','extra_price'))}
                for item in order.items.all()]}
            for order in user.orders.prefetch_related('items__options').all()]}
    response = JsonResponse(payload, json_dumps_params={'ensure_ascii':False, 'indent':2})
    response['Content-Disposition'] = 'attachment; filename="meus-dados-onmenu.json"'
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_POST
def request_deletion(request):
    if request.user.is_staff:
        messages.warning(request, 'Contas da equipe devem ser gerenciadas pelo responsável do estabelecimento.')
    elif request.POST.get('confirm_delete') != 'on':
        messages.warning(request, 'Confirme que deseja solicitar a exclusão da conta e dos dados pessoais.')
    else:
        DataRequest.objects.get_or_create(user=request.user, status='pending')
        messages.success(request, 'Solicitação recebida. O responsável acompanhará a exclusão dos seus dados.')
    return redirect('accounts:profile')


@permission_required('accounts.handle_data_request', raise_exception=True)
def staff_data_requests(request):
    return render(request, 'registration/data_requests.html',
        {'data_requests':DataRequest.objects.select_related('user').filter(status='pending')})


def anonymize_orders(orders):
    wanted = list(orders.values_list('id', flat=True))
    WhatsAppMessage.objects.filter(order_id__in=wanted).delete()
    Notification.objects.filter(order_id__in=wanted).delete()
    OrderItem.objects.filter(order_id__in=wanted).update(notes='')
    for attempt in PaymentAttempt.objects.filter(order_id__in=wanted):
        retained = {key:value for key,value in attempt.response_data.items()
                    if key in ('id','status','status_detail','installments','payment_method_id','last_four')}
        attempt.response_data = retained
        attempt.save(update_fields=['response_data'])
    PixPayment.objects.filter(order_id__in=wanted).update(qr_code_text='', qr_code_base64='', txid='')
    Order.objects.filter(id__in=wanted).update(user=None, customer_name='Cliente anonimizado', phone='',
        customer_email='', customer_cpf='', address='', address_street='', address_number='',
        address_complement='', address_city='', address_neighborhood='', notes='', whatsapp_opt_in=False)


@permission_required('accounts.handle_data_request', raise_exception=True)
@require_POST
@transaction.atomic
def complete_deletion(request, request_id):
    data_request = get_object_or_404(DataRequest.objects.select_for_update(of=('self',)).select_related('user'),
                                   pk=request_id, status='pending')
    user = data_request.user
    if not user or user.is_staff:
        messages.warning(request, 'Esta solicitação precisa ser revisada pelo responsável.')
    elif user.orders.filter(status__in=Order.ACTIVE_STATUSES).exists():
        messages.warning(request, 'Conclua os pedidos em andamento antes de atender a exclusão.')
    elif request.POST.get('confirm_delete') != 'on':
        messages.warning(request, 'Confirme a exclusão e a anonimização dos pedidos.')
    else:
        anonymize_orders(user.orders.all())
        user.delete()
        data_request.user = None
        data_request.status = 'completed'
        data_request.reviewed_by = request.user
        data_request.reviewed_at = timezone.now()
        data_request.save()
        messages.success(request, 'Conta excluída e dados pessoais dos pedidos anonimizados.')
    return redirect('accounts:staff_data_requests')
