from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


_LABEL_OVERRIDES = {
    "s3": "S3",
    "ec2": "EC2",
    "rds": "RDS",
    "iam": "IAM",
    "cdn": "CDN",
    "aws": "AWS",
    "api": "API",
    "cli": "CLI",
    "ui": "UI",
    "cloudfront": "CloudFront",
}


def display_label(value: object) -> str:
    raw_value = getattr(value, "value", value)
    words = str(raw_value).replace("_", " ").replace("-", " ").split()
    return " ".join(_LABEL_OVERRIDES.get(word.casefold(), word.capitalize()) for word in words)


templates.env.filters["display_label"] = display_label
