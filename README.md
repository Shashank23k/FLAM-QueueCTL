# FLAM-QueueCTL

## queuectl — Background Job Queue System (CLI-Based)

queuectl is a lightweight background job queue system designed to run through a command-line interface.
It supports multiple workers, persistent job storage, automatic retries using exponential backoff, and a Dead Letter Queue (DLQ) for jobs that permanently fail.
### 1. Setup Instructions — How to Run Locally
  #### **Requirements**
- **Python 3.8+**
- Recommended Terminal: **Windows CMD**
  > ⚠️ PowerShell may cause issues with JSON quoting during `enqueue` commands.

---

#### **Create & Activate Virtual Environment**

```bash
python -m venv .venv
.\.venv\Scripts\activate.bat
```
#### **Install Dependencies**
```bash
pip install typer rich
```
### 2. Usage Examples — CLI Commands with Outputs

#### **Enqueue Jobs**

```bash
# Enqueue a simple hello command
python queuectl.py enqueue "{\"id\": \"job-ok\", \"command\": \"echo Hello\"}"
# → Enqueued job-ok


# Enqueue a job that sleeps
python queuectl.py enqueue "{\"id\": \"job-sleep\", \"command\": \"powershell -Command Start-Sleep -Seconds 5\"}"
# → Enqueued job-sleep


# Enqueue a job that will fail
python queuectl.py enqueue "{\"id\":\"job-bad\",\"command\":\"powershell -Command \\\"exit 2\\\"\",\"max_retries\":2}"
# → Enqueued job-bad
```
#### **Start Workers**	
```bash
python queuectl.py worker start --count 1
```
Output will show job lifecycle progression 
Press Ctrl + C to stop workers gracefully
#### **View Queue State**	
```bash
python queuectl.py status
python queuectl.py list
```
#### **Dead Letter Queue Operations**	
```bash
python queuectl.py dlq list
python queuectl.py dlq retry job-bad
```
#### **Configuration Management**	
```bash
python queuectl.py config set backoff_base 3
python queuectl.py config get backoff_base
```
### **3. Architecture Overview**
#### **Job Lifecycle Diagram**
```text
     enqueue
        │
        ▼
   ┌───────────┐
   │ pending   │
   └─────┬─────┘
         │ worker claims job
         ▼
   ┌───────────┐
   │processing │
   └─────┬─────┘
    success│  │failure
           │  │
           ▼  ▼
   ┌───────────┐            attempts < max
   │ completed │ ←──────────── retry (state=pending, available_at delayed)
   └───────────┘
                      attempts > max
                            │
                            ▼
                    ┌──────────────┐
                    │ dead (DLQ)   │
                    └──────────────┘
```

| Term | Meaning |
|------|---------|
| <u>**retry**</u> | Job is returned to **pending** with delay and increased attempt count. |
| <u>**dead (DLQ)**</u> | Job has **exceeded max attempts** and is moved to the Dead Letter Queue. |
#### **Worker Logic** 
Workers continuously perform the following steps:

1. Poll the database for a job in the `pending` state that is ready to run.
2. Execute the job's command using `subprocess.run()`.
3. Update the job state based on the command's exit code:
   - `0` → mark job as **completed**
   - non-zero → increment attempt count and **retry** or move to **dead (DLQ)** if attempts exceeded
4. Sleep briefly when no jobs are ready (configurable idle delay).
#### **Persistence**
- All job records and worker heartbeat metadata are stored in `queue.db` (SQLite).
- WAL journaling mode ensures durability and allows concurrent worker processes.
### **4. Assumptions & Trade-offs**

| Design Choice                            | Reasoning                                                 | Trade-off                                   |
|------------------------------------------|-----------------------------------------------------------|----------------------------------------------|
| **SQLite as storage**                    | Lightweight, file-based, durable, easy to use            | Not ideal for large distributed scaling      |
| **Subprocess to execute commands**       | Allows generic shell commands                            | Security depends on user input safety        |
| **Exponential backoff retry**            | Prevents hammering failing operations                    | Retry intervals increase quickly             |
| **Worker loop instead of message broker**| Simple architecture without external dependencies         | Limited horizontal scalability               |
| **CMD recommended over PowerShell**      | Ensures JSON is parsed correctly when enqueueing commands | Requires users to switch terminal on Windows |

> This implementation prioritizes **clarity**, **reliability**, and **functional correctness** over cloud-scale distributed optimization.
### **5. Testing Instructions — How to Verify Functionality**
#### Test 1 — Successful Job
```bash
python queuectl.py enqueue "{\"id\":\"job-ok\",\"command\":\"echo Hello\"}"
python queuectl.py worker start --count 1
```
Expected: `job-ok`→ `completed`
#### Test 2 — Sleep Job
``` bash
python queuectl.py enqueue "{\"id\":\"job-sleep\",\"command\":\"powershell -Command Start-Sleep -Seconds 2\"}"
python queuectl.py worker start --count 1
```
Expected: completes after ~2 seconds
#### Test 3 — Failed Job Retries → DLQ
```bash
python queuectl.py enqueue "{\"id\":\"job-bad\",\"command\":\"powershell -Command \\\"exit 2\\\"\",\"max_retries\":2}"
python queuectl.py worker start --count 1
```
Wait ~8seconds
```bash
python queuectl.py dlq list
```
Expected: `job-bad` appears in `dead` state
#### Test 4 — Retry DLQ Job
```bash
python queuectl.py dlq retry job-bad
python queuectl.py worker start --count 1
```
### Web Dashboard
A live dashboard is available to visualize jobs and workers
#### Run Dashboard
```bash
python dashboard.py
```
#### Then open:
```bash
http://127.0.0.1:5000
```
Dashboard displays:
- Job counts by state
- Worker activity + heartbeat
- Recent job activity
### Bonus Features Implemented 

| Feature                   | Status | Description                                                     |
|---------------------------|:------:|-----------------------------------------------------------------|
| **Job Timeout Handling**  |   ✅   | Per-job `run_timeout` enforced in the worker subprocess.        |
| **Priority Queueing**     |   ✅   | Jobs ordered by `priority` (lower value = higher priority).     |
| **Scheduled / Delayed Jobs** | ✅  | Uses `available_at` timestamp + exponential backoff.            |
| **Job Output Logging**    |   ✅   | Captures and stores stdout in the `output` column.              |
| **Metrics & Execution Stats** | ✅ | `status` command + dashboard expose runtime system metrics.     |
| **Web Dashboard**         |   ✅   | Real-time monitoring UI implemented using Flask.                |

### 🎬 Demo Video

Watch the full demonstration here:  
➡️ **[Click to Watch Video](https://drive.google.com/file/d/1FCiSWzshSOW-BeEUIvCv9N59El4M4bjE/view?usp=sharing)**

