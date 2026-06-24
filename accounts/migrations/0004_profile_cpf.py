from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_profile_address_complement_profile_address_number_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='cpf',
            field=models.CharField(blank=True, max_length=14, verbose_name='CPF'),
        ),
    ]
