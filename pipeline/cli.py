"""CLI entry point for the ClaimCoach content pipeline."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

from pipeline.config import Config
from pipeline.db import ArticleStatus, Database


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_config_and_db(config_path: str | None) -> tuple[Config, Database]:
    cfg = Config.load(config_path)
    db_path = cfg.resolve_path(cfg.pipeline.database_path)
    db = Database(db_path)
    return cfg, db


def _run_simple_agent(agent_name: str, cfg: Config) -> dict:
    """Run agents that use simpler Agent base class."""
    # Convert config to dict
    config_dict = {
        "anthropic_api_key": cfg.anthropic.api_key,
        "blog_output_dir": cfg.blog.output_dir,
        "site_url": cfg.blog.site_url,
    }

    if agent_name == "atlas":
        from pipeline.agents.atlas import Atlas
        agent = Atlas(config_dict)
    elif agent_name == "rival":
        from pipeline.agents.rival import Rival
        config_dict["competitor_domains"] = getattr(cfg, "competitor_domains", [])
        config_dict["target_keywords"] = getattr(cfg, "target_keywords", [])
        agent = Rival(config_dict)
    elif agent_name == "remix":
        from pipeline.agents.remix import Remix
        config_dict["remix_types"] = ["twitter", "linkedin", "email", "youtube"]
        agent = Remix(config_dict)
    else:
        return {"status": "error", "error": f"Unknown agent: {agent_name}"}

    try:
        result = agent.run()
        return result
    except Exception as e:
        logging.error(f"{agent_name} failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


@click.group()
@click.option("--config", "-c", default=None, help="Path to config.yaml")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx, config, verbose):
    """ClaimCoach AI Content Marketing Pipeline."""
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


# ── Agent Commands ───────────────────────────────────────────


@cli.command()
@click.argument("agent_name", type=click.Choice(
    ["scout", "quill", "sage", "ezra", "herald", "lurker", "morgan", "atlas", "rival", "remix", "all"]
))
@click.pass_context
def run(ctx, agent_name):
    """Run a specific agent (or 'all' for full pipeline pass)."""
    from pipeline.scheduler import run_agent, run_all_agents

    cfg, db = get_config_and_db(ctx.obj["config_path"])

    # New agents (atlas, rival, remix) use simpler pattern
    if agent_name in ["atlas", "rival", "remix"]:
        result = _run_simple_agent(agent_name, cfg)
        click.echo(json.dumps(result, indent=2, default=str))
    elif agent_name == "all":
        results = run_all_agents(cfg, db)
        # Also run new agents
        for name in ["atlas", "rival", "remix"]:
            try:
                results[name] = _run_simple_agent(name, cfg)
            except Exception as e:
                results[name] = {"status": "error", "error": str(e)}
        for name, result in results.items():
            status = result.get("status", "unknown")
            click.echo(f"  {name}: {status}")
    else:
        result = run_agent(agent_name, cfg, db)
        click.echo(json.dumps(result, indent=2, default=str))


@cli.command()
@click.pass_context
def start(ctx):
    """Start the scheduler (runs agents on configured cron schedules)."""
    from pipeline.scheduler import start_scheduler

    cfg, db = get_config_and_db(ctx.obj["config_path"])
    click.echo("Starting pipeline scheduler...")
    click.echo("Agents will run on their configured schedules.")
    click.echo("Press Ctrl+C to stop.\n")
    start_scheduler(cfg, db)


# ── Pipeline Management ─────────────────────────────────────


@cli.command()
@click.pass_context
def status(ctx):
    """Show pipeline dashboard — article counts per status."""
    cfg, db = get_config_and_db(ctx.obj["config_path"])
    summary = db.get_pipeline_summary()

    click.echo("\n  ClaimCoach Content Pipeline — Status")
    click.echo("  " + "=" * 45)

    status_order = [
        ("backlog", "Backlog"),
        ("todo", "To Do"),
        ("in_progress", "In Progress"),
        ("editor_review", "Sage Scoring"),
        ("review", "Your Review"),
        ("revision", "Revision"),
        ("ready_to_publish", "Ready to Publish"),
        ("done", "Published"),
        ("amplified", "Amplified"),
        ("rejected", "Rejected"),
    ]

    for key, label in status_order:
        count = summary.get(key, 0)
        bar = "#" * min(count, 40)
        click.echo(f"  {label:<18} {count:>4}  {bar}")

    click.echo("  " + "-" * 45)
    click.echo(f"  {'Total':<18} {summary.get('total', 0):>4}")
    click.echo()


@cli.command("list")
@click.option("--status", "-s", "article_status", default=None,
              type=click.Choice([s.value for s in ArticleStatus]),
              help="Filter by status")
@click.option("--limit", "-n", default=20, help="Max articles to show")
@click.pass_context
def list_articles(ctx, article_status, limit):
    """List articles in the pipeline."""
    cfg, db = get_config_and_db(ctx.obj["config_path"])
    articles = db.query_articles(status=article_status, limit=limit)

    if not articles:
        click.echo("No articles found.")
        return

    click.echo(f"\n  {'ID':<14} {'Status':<18} {'Keyword':<40} {'Title':<40}")
    click.echo("  " + "-" * 110)

    for a in articles:
        title = (a.title or a.suggested_title or "")[:38]
        keyword = (a.target_keyword or "")[:38]
        click.echo(f"  {a.id:<14} {a.status:<18} {keyword:<40} {title:<40}")

    click.echo(f"\n  Total: {len(articles)} articles\n")


@cli.command()
@click.argument("article_id")
@click.pass_context
def show(ctx, article_id):
    """Show full details for an article."""
    cfg, db = get_config_and_db(ctx.obj["config_path"])
    article = db.get_article(article_id)

    if not article:
        click.echo(f"Article not found: {article_id}")
        return

    click.echo(f"\n  Article: {article.id}")
    click.echo("  " + "=" * 50)
    click.echo(f"  Title:         {article.title}")
    click.echo(f"  Status:        {article.status}")
    click.echo(f"  Keyword:       {article.target_keyword}")
    click.echo(f"  Category:      {article.content_category}")
    click.echo(f"  Target State:  {article.target_state}")
    click.echo(f"  Word Count:    {article.word_count}")
    click.echo(f"  Sage Score:    {article.sage_score}/100" if article.sage_score else "  Sage Score:    Not scored yet")
    click.echo(f"  SEO Score:     {article.seo_score}/20" if article.seo_score else "  SEO Score:     N/A")
    click.echo(f"  Readability:   {article.readability_score}" if article.readability_score else "  Readability:   N/A")
    click.echo(f"  Revision #:    {article.revision_count}")
    click.echo(f"  Published URL: {article.published_url}")
    click.echo(f"  Created:       {article.created_at}")
    click.echo(f"  Updated:       {article.updated_at}")

    if article.meta_description:
        click.echo(f"\n  Meta: {article.meta_description}")

    if article.revision_notes:
        click.echo(f"\n  Revision Notes:\n{article.revision_notes}")

    if article.markdown_content:
        preview = article.markdown_content[:500]
        click.echo(f"\n  Content Preview:\n  {preview}...")

    click.echo()


@cli.command()
@click.argument("article_id")
@click.argument("new_status", type=click.Choice([s.value for s in ArticleStatus]))
@click.pass_context
def move(ctx, article_id, new_status):
    """Manually move an article to a different status."""
    cfg, db = get_config_and_db(ctx.obj["config_path"])
    article = db.get_article(article_id)

    if not article:
        click.echo(f"Article not found: {article_id}")
        return

    old_status = article.status
    db.update_article(article_id, status=new_status)
    click.echo(f"Moved {article_id}: {old_status} -> {new_status}")


@cli.command()
@click.option("--count", "-n", default=10, help="Number of topics to move")
@click.pass_context
def promote(ctx, count):
    """Move top-scored backlog topics to 'todo' status."""
    cfg, db = get_config_and_db(ctx.obj["config_path"])
    backlog = db.query_articles(
        status=ArticleStatus.BACKLOG.value,
        limit=count,
        order_by="commercial_intent DESC, keyword_difficulty ASC",
    )

    if not backlog:
        click.echo("No topics in backlog.")
        return

    promoted = 0
    for article in backlog:
        db.update_article(article.id, status=ArticleStatus.TODO.value)
        click.echo(f"  Promoted: {article.target_keyword}")
        promoted += 1

    click.echo(f"\nPromoted {promoted} topics to 'todo'")


@cli.command()
@click.pass_context
def report(ctx):
    """Generate Morgan's weekly pipeline report."""
    from pipeline.agents.morgan import MorganAgent

    cfg, db = get_config_and_db(ctx.obj["config_path"])
    morgan = MorganAgent(config=cfg, db=db)
    report_text = morgan.generate_weekly_report()
    click.echo(report_text)


@cli.command()
@click.pass_context
def health(ctx):
    """Run Morgan's health checks."""
    from pipeline.agents.morgan import MorganAgent

    cfg, db = get_config_and_db(ctx.obj["config_path"])
    morgan = MorganAgent(config=cfg, db=db)
    result = morgan.run()

    click.echo(f"\n  Pipeline Health: {result['health'].upper()}")
    click.echo("  " + "=" * 40)

    for name, check in result["checks"].items():
        status = check.get("status", check.get("alert", "check failed"))
        icon = "OK" if "healthy" in str(status) or "on_track" in str(status) else "!!"
        click.echo(f"  [{icon}] {name}: {status}")

    if result["alerts"]:
        click.echo(f"\n  Alerts ({len(result['alerts'])}):")
        for alert in result["alerts"]:
            click.echo(f"    - {alert}")

    if result["spawn_actions"]:
        click.echo(f"\n  Recommended spawns:")
        for action in result["spawn_actions"]:
            click.echo(f"    - Run: pipeline run {action}")

    click.echo()


@cli.command()
@click.pass_context
def opportunities(ctx):
    """List pending community engagement opportunities (from Lurker)."""
    cfg, db = get_config_and_db(ctx.obj["config_path"])
    opps = db.query_opportunities(status="pending", limit=20)

    if not opps:
        click.echo("No pending opportunities.")
        return

    click.echo(f"\n  Pending Engagement Opportunities")
    click.echo("  " + "=" * 60)

    for opp in opps:
        click.echo(f"\n  [{opp.relevance_score:.2f}] {opp.thread_title}")
        click.echo(f"  Platform: {opp.platform} | r/{opp.subreddit}")
        click.echo(f"  URL: {opp.url}")
        if opp.draft_response:
            preview = opp.draft_response[:200]
            click.echo(f"  Draft: {preview}...")
        click.echo()


@cli.command()
@click.pass_context
def init(ctx):
    """Initialize the pipeline — create database and seed backlog."""
    cfg, db = get_config_and_db(ctx.obj["config_path"])

    click.echo("Initializing ClaimCoach Content Pipeline...")
    click.echo(f"  Database: {cfg.resolve_path(cfg.pipeline.database_path)}")

    # Run Scout to seed the backlog
    from pipeline.agents.scout import ScoutAgent
    scout = ScoutAgent(config=cfg, db=db)
    result = scout.run()

    click.echo(f"  Seeded backlog: {result.get('seed_topics_added', 0)} topics")
    click.echo(f"  Discovered: {result.get('discovered_topics', 0)} additional topics")
    click.echo(f"  Total backlog: {result.get('final_backlog', 0)} topics")

    # Create output directories
    output_dir = cfg.resolve_path(cfg.pipeline.blog_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    click.echo(f"  Blog output: {output_dir}")

    click.echo("\nPipeline initialized. Next steps:")
    click.echo("  1. Copy config.example.yaml to config.yaml and add API keys")
    click.echo("  2. Run 'pipeline promote -n 5' to move topics to the writing queue")
    click.echo("  3. Run 'pipeline run quill' to write an article")
    click.echo("  4. Run 'pipeline run sage' to review it")
    click.echo("  5. Run 'pipeline start' for autonomous operation")


if __name__ == "__main__":
    cli()
