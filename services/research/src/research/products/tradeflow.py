"""TradeFlow product profile (V1).

Every field below comes verbatim from the product facts supplied for this
implementation (2026-09-27, from the product owner). Nothing is inferred: a
capability not explicitly listed is deliberately absent, not guessed at.

In particular, TradeFlow does NOT currently do tax calculation, tax-return
preparation, bookkeeping, expense tracking, mileage tracking, accounting,
Making Tax Digital (MTD) compliance, bank integrations, payment processing,
payroll, CRM beyond saved customers, or automated accounting/tax advice (see
`out_of_scope`). The opportunity engine must be free to reject a research
insight about those topics for lack of product fit, not stretch this profile
to cover it - that is the whole point of this profile being separate from the
prompt (see `research.product_context`).

Pricing is unknown and intentionally left as `None`, not guessed at.
"""

from __future__ import annotations

from research.product_context import ProductCapability, ProductProfile

TRADEFLOW_PROFILE = ProductProfile(
    key="tradeflow",
    version="tradeflow-v1",
    name="TradeFlow",
    description=(
        "A lightweight management system for creating, managing and sending quotes, jobs and "
        "invoices. Particularly suited to tradespeople, freelancers and other service businesses "
        "that repeatedly perform similar work and/or work with recurring customers. The primary "
        "audience is currently UK sole traders and similar small service businesses, although "
        "TradeFlow is not technically limited to sole traders."
    ),
    audience=("UK sole traders", "tradespeople", "freelancers", "small service businesses"),
    capabilities=(
        ProductCapability(
            key="quotes",
            name="Quotes",
            description="Create and customise quotes and send them to customers by email.",
            problem_solved="Producing and sending a quote to a customer.",
        ),
        ProductCapability(
            key="jobs",
            name="Jobs",
            description="Create and customise jobs.",
            problem_solved="Structuring and tracking the work being done for a customer.",
        ),
        ProductCapability(
            key="invoices",
            name="Invoices",
            description="Create and customise invoices and send them to customers by email.",
            problem_solved="Billing a customer for completed work.",
        ),
        ProductCapability(
            key="reusable_templates",
            name="Reusable templates",
            description=(
                "Save an existing quote, job or invoice as a reusable template. Example: a "
                "mechanic repeatedly performing MOT-related work can build the relevant "
                "job/quote/invoice structure once and reuse it rather than rebuilding it each time."
            ),
            problem_solved=(
                "Rebuilding the same quote/job/invoice structure from scratch for recurring work."
            ),
        ),
        ProductCapability(
            key="saved_customers",
            name="Saved customers/clients",
            description=(
                "Save existing customer information and reuse it when creating future quotes, "
                "jobs or invoices."
            ),
            problem_solved="Re-entering the same customer's details every time they are worked for.",
        ),
        ProductCapability(
            key="earnings_overview",
            name="Earnings overview",
            description="The homepage displays total earnings based on the invoices that have been added.",
            problem_solved="Seeing, at a glance, how much has been earned from invoiced work.",
        ),
        ProductCapability(
            key="trade_specific_customisation",
            name="Trade-specific customisation",
            description=(
                "When creating a job, quote or invoice, select a relevant trade/business type "
                "(e.g. mechanic, general, software development, electrician); the selected type "
                "changes which fields are available so the document is more relevant to that type "
                "of work - e.g. car model/registration for a mechanic, or tools used and "
                "technology/stack for software development."
            ),
            problem_solved="Generic quote/job/invoice fields that do not match the specifics of a given trade.",
        ),
        ProductCapability(
            key="email_delivery",
            name="Email delivery",
            description="Quotes and invoices can be sent to customers by email.",
            problem_solved="Getting a quote or invoice in front of the customer without a separate step.",
        ),
    ),
    differentiators=(
        "Combines reusable work templates, reusable customer information, quotes, jobs, invoices "
        "and trade-specific fields/customisation in one place.",
        "Particularly relevant to businesses that repeatedly perform similar work or repeatedly "
        "deal with the same customers: create it once, save it, reuse it.",
    ),
    positioning=(
        "Reduces the repetitive administrative work involved in quoting, managing jobs and "
        "invoicing."
    ),
    limitations=(),  # none supplied beyond out_of_scope; nothing is invented here
    out_of_scope=(
        "tax calculation",
        "tax return",
        "tax-return",
        "bookkeeping",
        "book-keeping",
        "expense tracking",
        "expense management",
        "mileage tracking",
        "mileage logging",
        "accounting",
        "making tax digital",
        "mtd compliance",
        "bank integration",
        "bank feed",
        "payment processing",
        "payroll",
        "crm",
        "automated tax advice",
        "automated accounting advice",
        "tax-return preparation",
    ),
    pricing=None,  # unknown; deliberately not supplied
    marketing_objectives=(),  # none supplied yet
)
