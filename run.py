"""
Local Development Server Runner
Run with: python run.py
Then open http://localhost:8000 in your browser.
"""

import uvicorn

if __name__ == "__main__":
    print("\n⚡ Starting BA Account Swarm Dashboard on http://localhost:8000")
    print("👉 Press Ctrl+C to stop.\n")
    uvicorn.run("api.index:app", host="127.0.0.1", port=8000, reload=True)
