"""Command line interface for the Company Verification Agent."""

from __future__ import annotations

from typing import Optional

import typer

from app.services.company_check_service import (
    apply_human_review,
    list_checks_from_db,
    list_company_checks,
    load_company_check,
    run_company_check,
)

app = typer.Typer(
    help="Company Verification Agent CLI",
    no_args_is_help=True,
)


@app.command("check-company")
def check_company(
    company: str = typer.Option(..., "--company", "-c", help="Company name."),
    country: str = typer.Option(..., "--country", help="Country."),
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="Optional company domain."),
) -> None:
    """Run a preliminary company check."""
    if not company.strip():
        raise typer.BadParameter("Company name must not be empty.")
    if not country.strip():
        raise typer.BadParameter("Country must not be empty.")

    try:
        response = run_company_check(company_name=company, country=country, domain=domain)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    result = response.json_result
    if result is None:
        typer.echo("No result was generated.", err=True)
        raise typer.Exit(code=1)

    typer.echo("")
    typer.echo("Company check completed")
    typer.echo("-----------------------")
    typer.echo(f"Check ID: {response.check_id}")
    typer.echo(f"Company: {result.company.name}")
    typer.echo(f"Country: {result.company.country}")
    typer.echo(f"Domain: {result.company.domain or 'Not provided'}")
    typer.echo(f"Verification confidence: {result.risk.verification_confidence.value}")
    typer.echo(f"Verification risk: {result.risk.verification_risk.value}")
    typer.echo(f"Business risk: {result.risk.business_risk.value}")
    typer.echo(
        "Preliminary verification score (legacy): "
        f"{result.risk.preliminary_score} ({result.risk.preliminary_level.value})"
    )
    typer.echo(f"Human review status: {result.risk.human_review_status.value}")
    typer.echo(f"JSON path: outputs/json/company_check_{response.check_id}.json")
    typer.echo(f"Markdown report: {response.markdown_report_path}")
    typer.echo("")


@app.command("list-checks")
def list_checks_command() -> None:
    """List saved company checks from PostgreSQL."""
    checks = list_checks_from_db(limit=20)

    if not checks:
        typer.echo("No company checks found.")
        return

    for check in checks:
        typer.echo(
            f"{check['check_id']} | "
            f"{check['company_name']} | "
            f"{check['country']} | "
            f"{check.get('domain') or '-'} | "
            f"risk_score={check.get('risk_score')} | "
            f"risk_level={check.get('risk_level')} | "
            f"review={check.get('human_review_status')} | "
            f"{check.get('created_at')}"
        )


@app.command("show-check")
def show_check(
    check_id: int = typer.Option(..., "--check-id", help="Company check id."),
) -> None:
    """Show one saved company check."""
    result = load_company_check(check_id)

    if result is None:
        typer.echo(f"Check with id {check_id} was not found.", err=True)
        raise typer.Exit(code=1)

    typer.echo("")
    typer.echo(f"Check ID: {result.check_id}")
    typer.echo(f"Company: {result.company.name}")
    typer.echo(f"Country: {result.company.country}")
    typer.echo(f"Domain: {result.company.domain or 'Not provided'}")
    typer.echo(f"Verification confidence: {result.risk.verification_confidence.value}")
    typer.echo(f"Verification risk: {result.risk.verification_risk.value}")
    typer.echo(f"Business risk: {result.risk.business_risk.value}")
    typer.echo(
        "Preliminary verification score (legacy): "
        f"{result.risk.preliminary_score} ({result.risk.preliminary_level.value})"
    )
    typer.echo(f"Human review status: {result.risk.human_review_status.value}")
    typer.echo("")
    typer.echo("Risk factors:")

    for factor in result.risk.factors:
        typer.echo(f"- {factor.name}: {factor.impact} — {factor.explanation}")

    typer.echo("")


@app.command("review-check")
def review_check(
    check_id: int = typer.Option(..., "--check-id", help="Company check id."),
    decision: str = typer.Option(..., "--decision", help="approved, rejected, or edited."),
    final_score: Optional[int] = typer.Option(None, "--final-score", help="Final risk score, 0-100."),
    final_level: Optional[str] = typer.Option(None, "--final-level", help="low, medium, or high."),
    notes: str = typer.Option("", "--notes", help="Reviewer notes."),
) -> None:
    """Apply human review to a company check."""
    try:
        result = apply_human_review(
            check_id=check_id,
            decision=decision,
            final_score=final_score,
            final_level=final_level,
            notes=notes,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Human review saved")
    typer.echo("------------------")
    typer.echo(f"Check ID: {result.check_id}")
    typer.echo(f"Decision: {result.risk.human_review_status.value}")
    typer.echo(f"Final score: {result.risk.final_score}")
    typer.echo(f"Final level: {result.risk.final_level.value if result.risk.final_level else 'Not set'}")


if __name__ == "__main__":
    app()