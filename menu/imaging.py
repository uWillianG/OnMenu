"""Compressão das imagens enviadas pelo painel.

As fotos do cardápio chegam direto da câmera do celular do dono — meio megabyte
por item, dimensões de foto de câmera — e quem paga essa conta é o cliente no
4G abrindo o cardápio. Aqui elas são reduzidas ao que a tela realmente usa,
uma vez, no envio.

O formato original é preservado (JPEG continua JPEG, PNG continua PNG): trocar
para JPEG obrigaria a mexer na extensão do arquivo, e o ganho de peso vem quase
todo do redimensionamento, não da troca de codec.
"""

import logging
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Tetos por uso, medidos pelo tamanho real de exibição.
# A foto do item aparece no máximo no modal, que tem 560px de largura; 1000px
# cobre isso com folga em tela de alta densidade. O logo aparece a 80px — o
# original tinha 1254px, quinze vezes o necessário.
MENU_PHOTO_SIZE = (1000, 1000)
LOGO_SIZE = (320, 320)
MAX_SIZE = MENU_PHOTO_SIZE
JPEG_QUALITY = 82

# Abaixo deste peso o reprocessamento não paga o próprio custo.
MIN_BYTES = 60 * 1024

SUPPORTED_FORMATS = ('JPEG', 'PNG', 'WEBP')


def compress(file_obj, size=None, max_size=MAX_SIZE, quality=JPEG_QUALITY):
    """Devolve uma versão menor do arquivo de imagem, ou ``None``.

    ``None`` significa "use o original": arquivo já pequeno, formato que não
    tratamos, arquivo ilegível, ou resultado que não ficou menor que a entrada.
    Nunca levanta exceção — imagem é acessório, não pode derrubar um cadastro.
    """
    if size is None:
        size = _size_of(file_obj)
    if size is not None and size < MIN_BYTES:
        return None

    try:
        file_obj.seek(0)
        image = Image.open(file_obj)
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        logger.warning('Imagem ilegível no upload; mantendo o arquivo original.')
        return None

    source_format = (image.format or '').upper()
    if source_format not in SUPPORTED_FORMATS:
        return None

    try:
        # Foto de celular costuma vir "deitada", com a rotação só no EXIF: sem
        # isto, encolher a imagem grava a orientação errada de vez.
        image = ImageOps.exif_transpose(image)
        image.thumbnail(max_size, Image.LANCZOS)   # só reduz, nunca amplia
        buffer = _encode(image, source_format, quality)
    except (OSError, ValueError):
        logger.warning('Falha ao recomprimir a imagem; mantendo o arquivo original.')
        return None

    data = buffer.getvalue()
    if size is not None and len(data) >= size:
        return None      # já estava bem otimizada; reprocessar só pioraria
    return ContentFile(data)


def _encode(image, source_format, quality):
    buffer = BytesIO()
    if source_format == 'JPEG':
        # JPEG não tem canal alfa; converter evita erro em imagens RGBA/P.
        image.convert('RGB').save(
            buffer, 'JPEG', quality=quality, optimize=True, progressive=True,
        )
    elif source_format == 'PNG':
        image.save(buffer, 'PNG', optimize=True)
    else:
        image.save(buffer, 'WEBP', quality=quality, method=6)
    return buffer


def _size_of(file_obj):
    """Tamanho em bytes do arquivo, quando dá para descobrir sem lê-lo todo."""
    size = getattr(file_obj, 'size', None)
    if isinstance(size, int):
        return size
    try:
        current = file_obj.tell()
        file_obj.seek(0, 2)
        size = file_obj.tell()
        file_obj.seek(current)
        return size
    except (OSError, ValueError, AttributeError):
        return None


def compress_pending_upload(field_file, max_size=MAX_SIZE):
    """Comprime o arquivo recém-enviado de um ``ImageField``, antes de gravar.

    Só age sobre arquivo ainda não gravado no storage (``_committed`` falso), que
    é exatamente o caso de um upload novo. Assim a imagem é escrita uma única
    vez, já reduzida, e um save comum de outro campo não relê nem reprocessa o
    arquivo que já está no disco.
    """
    if not field_file or getattr(field_file, '_committed', True):
        return False

    compressed = compress(field_file.file, max_size=max_size)
    if compressed is None:
        return False

    # Mantém o nome escolhido pelo upload_to; só troca o conteúdo.
    field_file.file = compressed
    return True
