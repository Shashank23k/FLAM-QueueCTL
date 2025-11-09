# FLAM-QueueCTL

queuectl — Background Job Queue System (CLI-Based)

queuectl is a lightweight background job queue system designed to run through a command-line interface.
It supports multiple workers, persistent job storage, automatic retries using exponential backoff, and a Dead Letter Queue (DLQ) for jobs that permanently fail.
1. Setup Instructions — How to Run Locally
Requirements

Python 3.8+

Recommended terminal: Windows CMD
(PowerShell may break JSON quoting — avoid for enqueue commands)
#Create and Activate Virtual Environment
