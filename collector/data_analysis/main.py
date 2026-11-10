import typer
from data_analysis.user_metrics import export_user_metrics_to_csv
from data_analysis.data_preparation import data_preparation_start
from data_analysis.model_training import train_random_forest
from data_analysis.log2_model_training import log2_train_random_forest
from data_analysis.visualizations import generate_all_visualizations
from data_analysis.empirical_analysis import export_for_analysis
from utils.logger import logger

app = typer.Typer(name="analyze", help="Data analysis commands for git-h-index")

@app.command()
def export():
    logger.info("Starting user metrics export...")
    export_user_metrics_to_csv()
    logger.info("Export completed successfully")

@app.command()
def train(
    model_type: str = typer.Option("both", "--model-type", "-m", help="Model type to train: rf, log2, or both"),
    no_stars: bool = typer.Option(False, "--no-stars", help="Train without star-related features"),
):
    model_type = model_type.lower()
    if model_type not in ("rf", "log2", "both"):
        typer.echo(f"Error: Invalid model type '{model_type}'. Choose from: rf, log2, both")
        raise typer.Exit(1)
    mode = "NO-STARS" if no_stars else "FULL (with stars)"
    logger.info(f"Starting model training (type: {model_type}, mode: {mode})...")
    X_train, X_test, y_train, y_test = data_preparation_start(exclude_star_features=no_stars)
    suffix = "_no_stars" if no_stars else ""
    if model_type in ("rf", "both"):
        logger.info("Training Random Forest model...")
        train_random_forest(X_train, X_test, y_train, y_test, suffix=suffix)
    if model_type in ("log2", "both"):
        logger.info("Training Log2 Random Forest model...")
        log2_train_random_forest(X_train, X_test, y_train, y_test, suffix=suffix)
    logger.info("Model training completed")

@app.command()
def visualize(
    model_type: str = typer.Option("both", "--model-type", "-m", help="Model type to visualize: rf, log2, or both"),
    no_stars: bool = typer.Option(False, "--no-stars", help="Visualize no-stars model"),
):
    model_type = model_type.lower()
    if model_type not in ("rf", "log2", "both"):
        typer.echo(f"Error: Invalid model type '{model_type}'. Choose from: rf, log2, both")
        raise typer.Exit(1)
    mode = "NO-STARS" if no_stars else "FULL (with stars)"
    logger.info(f"Generating visualizations (model type: {model_type}, mode: {mode})...")
    if model_type in ("rf", "both"):
        logger.info("Generating visualizations for original RF model...")
        generate_all_visualizations(model_type='original', no_stars=no_stars)
    if model_type in ("log2", "both"):
        logger.info("Generating visualizations for Log2 RF model...")
        generate_all_visualizations(model_type='log2', no_stars=no_stars)
    logger.info("Visualizations generated successfully")

@app.command()
def empirical(
    top_n: int = typer.Option(20, "--top", "-n", help="Number of top developers to analyze"),
    min_h: int = typer.Option(10, "--min-h", "-h", help="Minimum h-index threshold"),
):
    logger.info(f"Starting empirical analysis (top {top_n}, min h-index {min_h})...")
    results = export_for_analysis(top_n=top_n, min_h_index=min_h)
    if 'error' in results:
        logger.error(results['error'])
    else:
        logger.info("Empirical analysis completed successfully")

@app.command()
def pipeline():
    logger.info("Starting full analysis pipeline...")
    logger.info("Step 1/4: Exporting user metrics...")
    export()
    logger.info("Step 2/4: Training models...")
    train(model_type="both")
    logger.info("Step 3/4: Generating visualizations...")
    visualize(model_type="both")
    logger.info("Step 4/4: Running empirical analysis...")
    empirical(top_n=20, min_h=10)
    logger.info("Full pipeline completed successfully")

if __name__ == "__main__":
    app()
