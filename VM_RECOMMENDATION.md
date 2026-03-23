# VM Recommendation for TCGweb Health Checker

## Current Configuration Analysis

Based on your code (`run-crawler-fast.sh` and `gcp_main_mpfast.py`):

- **Concurrent Workers**: 13 parallel processes
- **Crawl Depth**: 3 levels
- **Memory per Worker**: 1024 MB (default, configurable)
- **Total Websites**: ~467 sites (from `config/websites.csv`)
- **Technology Stack**: 
  - Playwright with Chromium (headless browser)
  - Python multiprocessing
  - Async HTTP requests (httpx)
  - Excel report generation

## Recommended VM Specifications

### Option 1: **Recommended (Balanced Performance/Cost)**
```
Machine Type: n1-standard-16
- vCPUs: 16
- RAM: 60 GB
- Zone: asia-east1-c (as configured in your code)
- Boot Disk: 50 GB SSD (standard persistent disk)
- Estimated Cost: ~$0.76/hour (~$18/day if running 24/7)
```

**Rationale:**
- **CPU**: 16 vCPUs provides headroom for 13 workers + main process + OS overhead
- **Memory**: 60 GB allows ~4.6 GB per worker (13 × 1 GB + overhead), with buffer for Playwright browsers
- **Disk**: 50 GB sufficient since HTML saving is disabled (`--no-save-html`)

### Option 2: **High Performance (Faster Completion)**
```
Machine Type: n1-standard-32
- vCPUs: 32
- RAM: 120 GB
- Zone: asia-east1-c
- Boot Disk: 100 GB SSD
- Estimated Cost: ~$1.52/hour (~$36/day)
```

**Use Case**: If you want to process all 467 sites faster, or if you plan to increase `--concurrent` beyond 13.

### Option 3: **Cost-Optimized (Slower but Cheaper)**
```
Machine Type: n1-standard-8
- vCPUs: 8
- RAM: 30 GB
- Zone: asia-east1-c
- Boot Disk: 50 GB SSD
- Estimated Cost: ~$0.38/hour (~$9/day)
```

**Trade-offs**: 
- May experience CPU contention with 13 workers on 8 vCPUs
- Memory might be tight (30 GB for 13 workers + overhead)
- Consider reducing `--concurrent` to 6-8 for this VM

## Memory Calculation

```
Base Requirements:
- 13 workers × 1024 MB = 13,312 MB (~13 GB)
- Playwright Chromium per worker: ~200-500 MB each = 2.6-6.5 GB
- Main process + Python overhead: ~2-4 GB
- OS and system processes: ~2-4 GB
- Buffer for spikes: ~5-10 GB

Total Minimum: ~25-35 GB
Recommended: 60 GB (Option 1) for comfortable operation
```

## Additional Recommendations

### 1. **Disk Space**
- Since `--no-save-html` is enabled, disk usage is minimal
- 50 GB is sufficient for:
  - OS (~10 GB)
  - Python environment (~2 GB)
  - Logs and reports (~5-10 GB)
  - Excel files (~1-5 GB)
  - Buffer (~20+ GB)

### 2. **Network**
- Ensure high network bandwidth (GCP default is usually sufficient)
- Consider enabling "Premium Network Tier" if crawling international sites

### 3. **Startup Script Configuration**
Your `startup-script-fast.sh` is already configured correctly. Ensure:
- VM has proper IAM permissions for auto-shutdown
- Service account has `compute.instances.stop` permission

### 4. **Monitoring**
Consider adding these flags to monitor resource usage:
```bash
# In run-crawler-fast.sh, you could add:
--max-mem-mb 1536  # Increase per-worker limit if needed
```

### 5. **Cost Optimization**
Since your VM auto-shuts down after completion:
- Use **Preemptible VMs** (up to 80% discount) if job completion time is flexible
- Use **Sustained Use Discounts** (automatic 20-30% off for sustained usage)
- Consider **Committed Use Discounts** if running regularly

## GCP Machine Type Comparison

| Machine Type | vCPUs | RAM | Est. Cost/hr | Best For |
|-------------|-------|-----|--------------|----------|
| n1-standard-8 | 8 | 30 GB | $0.38 | Budget, reduce concurrent to 8 |
| **n1-standard-16** | **16** | **60 GB** | **$0.76** | **Recommended** |
| n1-standard-32 | 32 | 120 GB | $1.52 | High performance |
| n1-highmem-16 | 16 | 104 GB | $0.95 | If memory is the bottleneck |
| n1-highcpu-16 | 16 | 14.4 GB | $0.71 | If CPU is the bottleneck (not recommended) |

## Final Recommendation

**Start with `n1-standard-16`** (Option 1):
- Best balance of performance and cost
- Adequate resources for 13 concurrent workers
- Can handle memory spikes from Playwright browsers
- Auto-shutdown minimizes costs

**Monitor and adjust**:
- If you see memory pressure, upgrade to `n1-standard-32` or `n1-highmem-16`
- If CPU is underutilized, you could reduce to `n1-standard-8` and lower `--concurrent` to 8
- If processing is too slow, increase to `n1-standard-32` and consider increasing `--concurrent` to 20+

## GCP Command to Create VM

```bash
gcloud compute instances create crawler-webcheck-mpfast \
  --zone=asia-east1-c \
  --machine-type=n1-standard-16 \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-standard \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --metadata-from-file startup-script=startup-script-fast.sh \
  --service-account=YOUR_SERVICE_ACCOUNT@PROJECT_ID.iam.gserviceaccount.com \
  --scopes=https://www.googleapis.com/auth/cloud-platform
```

Make sure the service account has:
- `roles/compute.instanceAdmin.v1` (for auto-shutdown)
- `roles/storage.objectAdmin` (if using Cloud Storage for reports)
