from contextlib import closing
from io import BytesIO
import logging
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from app.database import connect
from app.services.business_profile_service import (
    get_business_profile,
    save_business_profile,
)


logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000

SUPPORTED_FORMATS = {
    "PNG": ".png",
    "JPEG": ".jpg",
}

IMAGE_TYPES = {
    "logo": ("logo_path", "logos"),
    "qris": ("qris_path", "qris"),
    "signature": ("signature_path", "signatures"),
    "stamp": ("stamp_path", "stamps"),
}


class BusinessImageValidationError(ValueError):
    """Gambar usaha tidak memenuhi ketentuan."""


def _get_image_config(image_type: str) -> tuple[str, str]:
    config = IMAGE_TYPES.get(image_type)

    if config is None:
        raise BusinessImageValidationError(
            f"Jenis gambar tidak dikenal: {image_type}"
        )

    return config


def _read_image(source_path: Path) -> tuple[bytes, str]:
    with source_path.open("rb") as source_file:
        data = source_file.read(MAX_FILE_SIZE + 1)

    if len(data) > MAX_FILE_SIZE:
        raise BusinessImageValidationError(
            "Ukuran Gambar maksimal 5 MB."
        )

    try:
        with Image.open(BytesIO(data)) as image:
            extension = SUPPORTED_FORMATS.get(image.format)

            if extension is None:
                raise BusinessImageValidationError(
                    "Gunakan gambar dengan format PNG atau JPG"
                )

            if image.width * image.height > MAX_PIXELS:
                raise BusinessImageValidationError(
                    "Resolusi gambar maksimal 16 Megapixel."
                )

            image.load()

    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
    ) as error:
        raise BusinessImageValidationError(
            "Gambar tidak dapat dibaca atau file rusak."
        ) from error

    return data, extension

def save_business_image(
    database_path: Path,
    source_path: Path,
    image_type: str,
) -> bytes:
    field_name, directory_name = _get_image_config(image_type)

    database_path = Path(database_path)
    source_path = Path(source_path)

    with closing(connect(database_path)) as connection:
        profile = get_business_profile(connection)

        if profile is None:
            raise BusinessImageValidationError(
                "Simpan profil usaha terlebih dahulu "
                "sebelum menambahkan gambar."
            )

        data, extension = _read_image(source_path)

        relative_path = (
            Path("assets")
            / directory_name
            / f"{uuid4().hex}{extension}"
        )

        destination = database_path.parent / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)

        created = False

        try:
            with destination.open("xb") as destination_file:
                created = True
                destination_file.write(data)

            save_business_profile(
                connection,
                {field_name: relative_path.as_posix()},
            )

        except Exception:
            if created:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    logger.exception(
                        "Gagal membersihkan gambar sementara"
                    )

            raise

    return data


def load_business_image(
    database_path: Path,
    image_type: str,
) -> bytes | None:
    field_name, directory_name = _get_image_config(image_type)

    database_path = Path(database_path)

    with closing(connect(database_path)) as connection:
        profile = get_business_profile(connection)

    if profile is None or not profile.get(field_name):
        return None

    image_directory = (
        database_path.parent / "assets" / directory_name
    ).resolve()

    image_path = (
        database_path.parent / profile[field_name]
    ).resolve()

    if image_path.parent != image_directory:
        raise BusinessImageValidationError(
            "Lokasi gambar tersimpan tidak valid. "
            "Silakan pilih ulang gambar."
        )

    if not image_path.is_file():
        raise BusinessImageValidationError(
            "File gambar tidak ditemukan. "
            "Silakan pilih ulang gambar."
        )

    data, _ = _read_image(image_path)

    return data

