from contextlib import contextmanager
from io import BytesIO
import logging
from pathlib import Path
from uuid import uuid4

from PIL import Image

from app.services.business_image_service import (
    BusinessImageValidationError,
    IMAGE_TYPES,
    SUPPORTED_FORMATS,
    load_business_image
)


logger = logging.getLogger(__name__)


@contextmanager
def snapshot_business_images(database_path: Path, profile: dict):
    database_path = Path(database_path).resolve()
    created_files = []
    directory = None
    snapshot = {}

    try:
        for image_type, (field, _) in IMAGE_TYPES.items():
            snapshot[field] = None

            if not profile.get(field):
                continue

            data = load_business_image(database_path, image_type)

            if data is None:
                raise BusinessImageValidationError(
                    f"Gambar dengan Format {image_type} tidak tersedia."
                )

            with Image.open(BytesIO(data)) as image:
                extension = SUPPORTED_FORMATS[image.format]

            if directory is None:
                parent = database_path.parent / "assets" / "documents"
                parent.mkdir(parents = True, exist_ok = True)

                candidate = parent / uuid4().hex
                candidate.mkdir()
                directory = candidate

            destination = directory / f"{image_type}{extension}"

            with destination.open("xb") as output:
                created_files.append(destination)
                output.write(data)

            snapshot[field] = destination.relative_to(
                database_path.parent
            ).as_posix()

        yield snapshot

    except BaseException:
        for path in reversed(created_files):
            try:
                path.unlink(missing_ok = True)
            except OSError:
                logger.exception(
                    "Gagal membersihkan salinan gambar: %s",
                    path,
                )

        if directory is not None:
            try:
                directory.rmdir()
            except OSError:
                logger.exception(
                    "Gagal membersihkan folder: %s",
                    directory,
                )

        raise