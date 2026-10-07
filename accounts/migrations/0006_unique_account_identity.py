from django.db import migrations, models


def unique_email_index(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    # O User é do app auth: índice SQL mantém a proteção no próprio registro,
    # inclusive em saves do admin e no cadastro concorrente.
    quote = schema_editor.quote_name
    table = quote(User._meta.db_table)
    schema_editor.execute(
        f'CREATE UNIQUE INDEX {quote("accounts_unique_user_email")} '
        f'ON {table} (LOWER(TRIM(email))) WHERE email <> \'\''
    )


def remove_email_index(apps, schema_editor):
    schema_editor.execute(f'DROP INDEX {schema_editor.quote_name("accounts_unique_user_email")}')


class Migration(migrations.Migration):
    dependencies = [('accounts', '0005_accessattempt'), ('auth', '0012_alter_user_first_name_max_length')]
    operations = [
        migrations.RunPython(unique_email_index, remove_email_index),
        migrations.AddConstraint(model_name='profile', constraint=models.UniqueConstraint(
            fields=['cpf'], condition=~models.Q(cpf=''), name='unique_nonempty_profile_cpf')),
    ]
