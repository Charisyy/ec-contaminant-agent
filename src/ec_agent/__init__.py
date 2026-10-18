"""Full-catalog contaminant database. Procurement flags never restrict ingestion."""
import os
os.environ.setdefault('OTEL_SDK_DISABLED','true')
os.environ.setdefault('CREWAI_TELEMETRY_ENABLED','false')
os.environ.setdefault('CREWAI_TRACING_ENABLED','false')
__version__ = "0.2.0"
