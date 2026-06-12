.PHONY: check run serve

UV = uv run python

check:
	$(UV) -c "import scraper, storage, server; print('imports ok')"
	@test -f .env || (echo "ERROR: .env not found — copy .env.example and fill in credentials" && exit 1)
	$(UV) -c "from dotenv import load_dotenv; import os; load_dotenv(); t=os.getenv('NOVOGENE_TOKEN',''); p=os.getenv('NOVOGENE_PROJECTS',''); print('token:', 'set' if t else 'MISSING'); print('projects:', p or 'MISSING')"

run:
	$(UV) main.py scrape

serve:
	$(UV) main.py serve
