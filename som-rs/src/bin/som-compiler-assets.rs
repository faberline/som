//! Install a new immutable asset package for the matching compiled release.
use clap::Parser;
use std::path::PathBuf;
#[derive(Parser)]
struct Args {
    #[arg(long)]
    destination: PathBuf,
    #[arg(long)]
    formatter: PathBuf,
}
fn main() {
    let args = Args::parse();
    match som::compiler::package_assets(&args.destination, &args.formatter) {
        Ok(manifest) => println!("{}", serde_json::to_string_pretty(&manifest).unwrap()),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
