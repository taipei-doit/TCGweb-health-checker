# Fast vs Self-Queue Version Comparison

## Overview

Both versions use multiprocessing to crawl websites in parallel, but they differ in how they manage worker processes and handle memory.

## Key Differences

### 1. **Worker Process Management**

#### **Fast Version** (`gcp_main_mpfast.py`)
- Uses **`multiprocessing.Pool`** with `maxtasksperchild=1`
- **Automatic process recycling**: Each worker process restarts after completing ONE task
- Simpler implementation, relies on Python's built-in Pool
- Less control over individual workers

```python
with multiprocessing.Pool(processes=args.concurrent, maxtasksperchild=1) as pool:
    results = pool.imap_unordered(run_crawl_task, websites_to_process)
```

#### **Self-Queue Version** (`gcp_main_mpselfqueue.py`)
- Uses **manual `Process` and `Queue`** management
- **Custom worker loop**: Workers run continuously and pull tasks from a queue
- More control: Can monitor memory, handle restarts dynamically
- Workers stay alive across multiple tasks (until memory limit or error)

```python
task_queue = Queue()
result_queue = Queue()
# Manual Process creation and management
worker_pool = {}
```

### 2. **Memory Management**

#### **Fast Version**
- **No memory monitoring**: Relies on `maxtasksperchild=1` to restart workers after each task
- Memory is "reset" naturally because each worker dies after one task
- Simpler but less efficient (process creation overhead)

#### **Self-Queue Version**
- **Active memory monitoring** using `psutil`
- Checks memory **before accepting each new task**
- If memory exceeds `--max-mem-mb` (default 1024 MB), worker requests restart
- More efficient: Workers can handle multiple tasks if memory allows

```python
# Memory check before each task
memory_mb = process.memory_info().rss / 1024 / 1024
if memory_mb > max_mem_mb:
    result_queue.put(("RESTART", worker_id))
    break
```

### 3. **Process Lifecycle**

#### **Fast Version**
```
Task 1 → Worker starts → Process task → Worker dies → New worker for Task 2
```
- **One task per process**: Always fresh process
- Higher overhead from process creation/destruction
- Guaranteed memory cleanup

#### **Self-Queue Version**
```
Worker starts → Task 1 → Task 2 → Task 3 → ... (until memory limit or error)
```
- **Multiple tasks per process**: Reuses same process
- Lower overhead, more efficient
- Memory can accumulate (hence monitoring needed)

### 4. **Error Handling**

#### **Fast Version**
- Errors handled by Pool's built-in mechanisms
- Failed tasks return `None`
- Simpler error reporting

#### **Self-Queue Version**
- **Custom error handling** with detailed reporting
- Sends `("FAILED", site_name)` tuples to result queue
- Can track which specific site failed
- More granular error information

### 5. **Progress Tracking**

#### **Fast Version**
- Progress shown after each task completes
- Uses `imap_unordered` for real-time results

#### **Self-Queue Version**
- **Real-time progress tracking** with detailed counters
- Shows: `processed_count / total_tasks (success: X, failed: Y)`
- More detailed progress information

### 6. **Worker Restart Logic**

#### **Fast Version**
- Automatic: Pool handles restarts via `maxtasksperchild=1`
- No manual intervention needed

#### **Self-Queue Version**
- **Manual restart logic**:
  - Worker detects memory limit → sends `("RESTART", worker_id)`
  - Main process receives restart request
  - Old worker is terminated gracefully
  - New worker with same ID is spawned
  - Task queue continues seamlessly

```python
# Worker requests restart
if memory_mb > max_mem_mb:
    result_queue.put(("RESTART", worker_id))
    
# Main process handles restart
if isinstance(result, tuple) and result[0] == "RESTART":
    old_worker.terminate()
    start_new_worker(worker_id_to_restart)
```

## Performance Comparison

| Aspect | Fast Version | Self-Queue Version |
|--------|-------------|-------------------|
| **Process Overhead** | Higher (restart per task) | Lower (reuse processes) |
| **Memory Efficiency** | Lower (always fresh) | Higher (monitored reuse) |
| **CPU Efficiency** | Lower (process creation cost) | Higher (less overhead) |
| **Memory Safety** | Guaranteed (always fresh) | Monitored (may accumulate) |
| **Code Complexity** | Simpler | More complex |
| **Control Level** | Low (Pool manages) | High (manual control) |

## When to Use Which?

### Use **Fast Version** (`mpfast`) when:
- ✅ You want **simpler code** and less maintenance
- ✅ Memory leaks are a concern (guaranteed cleanup)
- ✅ You don't need fine-grained control
- ✅ Process creation overhead is acceptable
- ✅ You prefer Python's built-in solutions

### Use **Self-Queue Version** (`mpselfqueue`) when:
- ✅ You need **better performance** (less process overhead)
- ✅ You want **memory monitoring** and smart restarts
- ✅ You need **detailed error tracking** per site
- ✅ You want **more control** over worker lifecycle
- ✅ You're processing many sites and want efficiency

## Configuration

Both versions use the **same command-line arguments**:

```bash
--depth 3 --concurrent 13 --no-save-html --no-pagination
```

The only difference is:
- **Fast**: Uses `gcp_main_mpfast.py`
- **Self-Queue**: Uses `gcp_main_mpselfqueue.py`

## Recommendation

**For your use case (467 websites, 13 concurrent workers):**

1. **Start with Self-Queue** (`mpselfqueue`):
   - Better performance for large batches
   - Memory monitoring prevents OOM issues
   - More efficient process reuse
   - Better for long-running crawls

2. **Fall back to Fast** (`mpfast`) if:
   - You experience memory issues despite monitoring
   - You prefer simpler code
   - Process overhead is not a concern

## Code Location

- **Fast**: `gcp_main_mpfast.py` + `run-crawler-fast.sh` + `startup-script-fast.sh`
- **Self-Queue**: `gcp_main_mpselfqueue.py` + `run-crawler-selfqueue.sh` + `startup-script-selfqueue.sh`

Both scripts are identical except for the `PYTHON_SCRIPT` variable pointing to different Python files.
