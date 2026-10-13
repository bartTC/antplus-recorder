# Show the recipes
default:
    @just --list --unsorted

# Run the dashboard from the repo; recordings and devices.toml go to the current folder
run *options:
    uv run antplus-recorder {{options}}

# Only display, write no files
watch:
    uv run antplus-recorder --no-record

# Build the FIT files for a recorded CSV, e.g. after a crash
fit csv:
    uv run antplus-recorder --fit-from {{quote(csv)}}

# Run the tests
test *args:
    uv run pytest {{args}}

# Lint with ruff
lint:
    uv run ruff check src tests

# Build sdist and wheel into dist/
build:
    rm -rf dist && uv build

# Build, sign and notarize the macOS app into dist/macos (Apple Silicon)
app:
    packaging/macos/build.sh
