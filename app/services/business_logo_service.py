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


class LogoValidationError(ValueError):
    """Gambar tidak memenuhi ketentuan logo"""


def _read_image(source_path: Path) -> tuple[bytes, str]:
    with source_path.open("rb") as source_file:
        data = source_file.read(MAX_FILE_SIZE + 1)

    if len(data) > MAX_FILE_SIZE:
        raise LogoValidationError(
            "Ukuran Logo maksimal 5 MB."
        )

    try:
        with Image.open(BytesIO(data)) as image:
            extension = SUPPORTED_FORMATS.get(image.format)

            if extension is None:
                raise LogoValidationError(
                    "Logo harus berupa gambar PNG atau JPG."
                )

            if image.width * image.height > MAX_PIXELS:
                raise LogoValidationError(
                    "Resolusi logo terlalu besar. "
                    "Gunakan gambar maksimal 16 megapixel"
                )

            image.load()

    except(
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
    ) as error:
        raise LogoValidationError(
            "Gambar tidak dapat dibaca atau file rusak."
        )

    return data, extension


def save_business_logo(
    database_path: Path,
    source_path: Path,
) -> bytes:
    with closing(connect(database_path)) as connection:
        profile = get_business_profile(connection)

        if profile is None:
            raise LogoValidationError(
                "Simpan profil usaha terlebih dahulu "
                "sebelum menambahkan logo."
            )

        data, extension = _read_image(source_path)

        relative_path = (
            Path("assets")
            / "logos"
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
                {"logo_path": relative_path.as_posix()},
            )

        except Exception:

            if created:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    logger.exception(
                        "Gagal membersihkan file logo sementara"
                    )

            raise

    return data


def load_business_logo(
    database_path: Path,
) -> bytes | None:
    database_path = Path(database_path)

    with closing(connect(database_path)) as connection:
        profile = get_business_profile(connection)

    if profile is None or not profile.get("logo_path"):
        return None

    logo_directory = (
        database_path.parent / "assets" / "logos"
    ).resolve()

    logo_path = (
        database_path.parent / profile["logo_path"]
    ).resolve()

    if logo_path.parent != logo_directory:
        raise LogoValidationError(
            "Lokasi logo yang tersimpan tidak valid. "
            "Silahkan pilih ulang logo."
        )

    if not logo_path.is_file():
        raise LogoValidationError(
            "File logo tidak ditemukan. "
            "Silahkan pilih ulang logo."
        )

    data, _ = _read_image(logo_path)

    return data