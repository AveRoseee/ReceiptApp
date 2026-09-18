from pathlib import Path

from app.services.business_image_service import (
    BusinessImageValidationError,
    load_business_image,
    save_business_image
)


LogoValidatorError = BusinessImageValidationError


def save_business_logo(
    database_path: Path,
    source_path: Path,
) -> bytes:
    return save_business_image(
        database_path,
        source_path,
        "logo",
    )

def load_business_logo(
    database_path: Path,
) -> bytes | None:
    return load_business_image(
        database_path,
        "logo",
    )