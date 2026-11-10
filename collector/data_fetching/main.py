import typer

from data_fetching.github_parser import fetch_next_user_repositories, process_batch_of_users
from data_fetching.users_fetching.fetch_user_links import parse_user_links_batch, reset_links_parse_timestamps
from data_fetching.users_fetching.fetch_user_profile import enrich_user_profiles
from data_fetching.users_fetching.popular_users import process_popular_users
from data_storage.database import DatabaseManager
from utils.logger import logger

app = typer.Typer(name="fetch", help="GitHub data fetching commands for git-h-index")

@app.command()
def users(resume: bool = typer.Option(True, "--resume/--no-resume", help="Resume from checkpoint"),):
    logger.info("Starting popular users fetch...")
    process_popular_users()

@app.command()
def repos(
    batch_size: int = typer.Option(6, "--batch-size", "-b", help="Users per batch"),
    continuous: bool = typer.Option(True, "--continuous/--single", help="Continuously fetch or single batch"),
):
    if continuous:
        logger.info("Starting continuous repository fetching...")
        while fetch_next_user_repositories():
            pass
        logger.info("All user repository data has been fetched successfully")
    else:
        logger.info(f"Processing single batch of {batch_size} users...")
        process_batch_of_users(batch_size)

@app.command()
def profiles(
    batch_size: int = typer.Option(120, "--batch-size", "-b", help="Users per batch"),
    max_workers: int = typer.Option(24, "--workers", "-w", help="Max parallel workers"),
):
    logger.info("Starting user profile enrichment...")
    while enrich_user_profiles(batch_size=batch_size, max_workers=max_workers):
        pass

@app.command()
def links(
    batch_size: int = typer.Option(120, "--batch-size", "-b", help="Users per batch"),
    max_workers: int = typer.Option(24, "--workers", "-w", help="Max parallel workers"),
    days: int = typer.Option(30, "--days", "-d", help="Days since last update"),
    no_html: bool = typer.Option(False, "--no-html", help="Disable HTML fallback scraping"),
):
    logger.info("Starting user links parsing...")
    parse_user_links_batch(
        batch_size=batch_size,
        max_workers=max_workers,
        days_since_update=days,
        fetch_html_fallback=not no_html
    )

@app.command()
def reset_links(keep_failed: bool = typer.Option(True, "--keep-failed/--reset-failed", help="Keep failed users"),):
    logger.info("Resetting links parse timestamps...")
    reset_links_parse_timestamps(keep_failed_users=keep_failed)

@app.command()
def reset_last_updated(
    keep_failed: bool = typer.Option(True, "--keep-failed/--reset-failed", help="Keep failed users"),
    reset_date: str = typer.Option("1900-01-01T00:00:00", "--date", "-d", help="Date to set (default: 1900-01-01 to force re-fetch)"),
):
    logger.info(f"Resetting last_updated timestamps to {reset_date}...")
    db = DatabaseManager()
    reset_count = db.reset_last_updated_time(keep_failed_users=keep_failed, reset_date=reset_date)
    logger.info(f"Reset last_updated for {reset_count} users")

@app.command()
def parse_all(
    repos_batch: int = typer.Option(6, "--repos-batch", help="Repository batch size"),
    profiles_batch: int = typer.Option(120, "--profiles-batch", help="Profiles batch size"),
    profiles_workers: int = typer.Option(24, "--profiles-workers", help="Profiles workers"),
    links_batch: int = typer.Option(120, "--links-batch", help="Links batch size"),
    links_workers: int = typer.Option(24, "--links-workers", help="Links workers"),
    keep_failed: bool = typer.Option(True, "--keep-failed/--reset-failed", help="Keep failed users during resets"),
):
    logger.info("Starting full parse-all pipeline...")

    logger.info("Step 1/7: Fetching popular users...")
    process_popular_users()

    logger.info("Step 2/7: Fetching repositories...")
    while fetch_next_user_repositories():
        pass
    logger.info("Repository fetching complete")

    logger.info("Step 3/7: Resetting last_updated timestamps...")
    db = DatabaseManager()
    reset_count = db.reset_last_updated_time(keep_failed_users=keep_failed)
    logger.info(f"Reset last_updated for {reset_count} users")

    logger.info("Step 4/7: Resetting profile data (bio, company, location, etc.)...")
    reset_count = db.reset_profile_data(keep_failed_users=keep_failed)
    logger.info(f"Reset profile data for {reset_count} users")

    logger.info("Step 5/7: Enriching user profiles...")
    enrich_user_profiles(batch_size=profiles_batch, max_workers=profiles_workers)

    logger.info("Step 6/7: Resetting links parse timestamps...")
    reset_links_parse_timestamps(keep_failed_users=keep_failed)

    logger.info("Step 7/7: Parsing user links...")
    parse_user_links_batch(batch_size=links_batch, max_workers=links_workers)

    logger.info("Parse-all pipeline completed!")


if __name__ == "__main__":
    app()
