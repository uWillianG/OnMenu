# Migra os grupos de opção legados (por item) para o catálogo global de complementos.
from django.db import migrations


def forwards(apps, schema_editor):
    ItemOptionGroup = apps.get_model('menu', 'ItemOptionGroup')
    ComplementGroup = apps.get_model('menu', 'ComplementGroup')
    ComplementChoice = apps.get_model('menu', 'ComplementChoice')

    # Deduplica por (restaurant_id, nome do grupo). Grupos de mesmo nome são
    # idênticos no seed atual, então a primeira ocorrência define as opções.
    created = {}
    for old_group in ItemOptionGroup.objects.select_related(
        'menu_item__category'
    ).prefetch_related('choices').all():
        item = old_group.menu_item
        restaurant_id = item.category.restaurant_id
        key = (restaurant_id, old_group.name)

        new_group = created.get(key)
        if new_group is None:
            new_group = ComplementGroup.objects.create(
                restaurant_id=restaurant_id,
                name=old_group.name,
                selection_type='single' if old_group.required else 'multiple',
                required=old_group.required,
                display_order=old_group.display_order,
            )
            for choice in old_group.choices.all():
                ComplementChoice.objects.create(
                    group=new_group,
                    name=choice.name,
                    extra_price=choice.extra_price,
                    display_order=choice.display_order,
                )
            created[key] = new_group

        item.complement_groups.add(new_group)


def backwards(apps, schema_editor):
    # Sem reversão de dados; o schema é revertido pelas migrações vizinhas.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('menu', '0005_add_complement_catalog'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
