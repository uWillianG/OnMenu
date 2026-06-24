"""Backend de autenticação que aceita e-mail ou nome de usuário.

Clientes acessam o sistema pelo **e-mail** (o nome de usuário é um @handle
gerado automaticamente no cadastro), mas a equipe (staff) ainda pode entrar
pelo usuário. Este backend resolve o identificador informado tanto por
``username`` quanto por ``email`` (case-insensitive) e segue a checagem de
senha padrão do ``ModelBackend``.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        try:
            user = UserModel.objects.get(
                Q(username__iexact=username) | Q(email__iexact=username)
            )
        except UserModel.DoesNotExist:
            # Roda o hasher mesmo sem usuário para mitigar timing attacks.
            UserModel().set_password(password)
            return None
        except UserModel.MultipleObjectsReturned:
            # Identificador ambíguo (ex.: e-mail repetido em contas antigas):
            # nega por segurança em vez de adivinhar a conta.
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
