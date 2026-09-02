"""
AWS Lambda entry point.

Lambda calls a function with an event; FastAPI speaks ASGI. Mangum sits between
them: it turns an API Gateway HTTP API payload into an ASGI scope, runs the app,
and turns the response back into what API Gateway expects. The application does
not know it is running in Lambda, and nothing in app.py had to change for this.

`lifespan="on"` matters. FastAPI does its startup work - preparing storage and
writing the seed rows - in the lifespan handler, and without this Mangum would
skip it and the first request would hit an empty table. It runs once per
execution environment, not once per request.
"""

from mangum import Mangum

from app import app

handler = Mangum(app, lifespan="on")
