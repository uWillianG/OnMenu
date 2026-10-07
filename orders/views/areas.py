from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render

from ..area_forms import CityForm, NeighborhoodForm
from ..models import City, Neighborhood


@staff_member_required
def delivery_areas(request):
    city_form = CityForm()
    neighborhood_form = NeighborhoodForm()
    if request.method == 'POST':
        if request.POST.get('kind') == 'city':
            city_form = CityForm(request.POST)
            form = city_form
        else:
            neighborhood_form = NeighborhoodForm(request.POST)
            form = neighborhood_form
        if form.is_valid():
            form.save()
            messages.success(request, 'Região cadastrada.')
            return redirect('orders:delivery_areas')
    return render(request, 'orders/delivery_areas.html', {'cities':City.objects.prefetch_related('neighborhoods'),
        'city_form':city_form, 'neighborhood_form':neighborhood_form})


@staff_member_required
def edit_delivery_area(request, kind, pk):
    if kind == 'city':
        instance = get_object_or_404(City, pk=pk)
        form_type = CityForm
    elif kind == 'neighborhood':
        instance = get_object_or_404(Neighborhood, pk=pk)
        form_type = NeighborhoodForm
    else:
        from django.http import Http404
        raise Http404
    form = form_type(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Região atualizada. Desativar a região impede novas entregas.')
        return redirect('orders:delivery_areas')
    return render(request, 'orders/delivery_areas.html', {'edit_form':form, 'editing':True})
