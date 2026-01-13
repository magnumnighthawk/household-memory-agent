# Household Memory Agent

A simple command-line tool for storing and retrieving household-related notes, receipts, invoices, and service records using a local SQLite database with full-text search.

## Features
- Add notes, receipts, or service records with tags and sources
- Search and retrieve information using natural language queries
- Citations and confidence scoring for answers

## Usage

1. **Initialize the database:**
   ```sh
   python memory_agent.py init
   ```

2. **Add a memory item:**
   ```sh
   python memory_agent.py add --title "Boiler Service" --content "Serviced by ABC Heating on 2025-12-01" --tags "boiler,service,2025"
   ```

3. **Ask a question:**
   ```sh
   python memory_agent.py ask "When was the boiler last serviced?"
   ```

## Requirements
- Python 3.10+
- Install dependencies:
  ```sh
  pip install aiosqlite typer pydantic rich
  ```

## Notes
- Data is stored in `household_memory.sqlite3` in the current directory by default.
- All data stays local to your machine.
