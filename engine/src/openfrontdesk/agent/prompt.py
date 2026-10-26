from datetime import datetime

from jinja2 import Environment, PackageLoader, StrictUndefined

from openfrontdesk.business import BusinessProfile
from openfrontdesk.timefmt import long_label

_templates = Environment(
    loader=PackageLoader("openfrontdesk.agent", "templates"),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def render_system_prompt(
    business: BusinessProfile, *, now: datetime, customer_phone: str | None
) -> str:
    return _templates.get_template("system_prompt.md.j2").render(
        business=business,
        now=long_label(now, business.tz),
        today=now.astimezone(business.tz).date().isoformat(),
        customer_phone=customer_phone,
    )
