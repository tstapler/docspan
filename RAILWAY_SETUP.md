# Railway Deployment Setup

## Volume Configuration (Required for State Persistence)

The sync state file (`.sync_state.json`) needs persistent storage to survive across deployments.

### Step 1: Create a Volume in Railway

1. Go to your Railway project dashboard
2. Click on your service
3. Go to the **"Variables"** or **"Settings"** tab
4. Find the **"Volumes"** section
5. Click **"Add Volume"**
6. Configure:
   - **Mount Path**: `/data`
   - **Name**: `sync-state-volume` (or any name you prefer)
7. Click **"Add"**

### Step 2: Verify Volume is Mounted

After deployment, check the logs. You should see:
```
Saved sync state to /data/.sync_state.json
```

If you see `/app/.sync_state.json` instead, the volume is not properly mounted.

### Alternative: Use Environment Variable

If you want to use a different path, set the `STATE_DIR` environment variable:

```bash
STATE_DIR=/your/custom/path
```

## How It Works

- **Local development**: State file is stored in project directory
- **Railway with volume**: State file is stored in `/data` volume (persistent)
- **Railway without volume**: State file is stored in `/app` (ephemeral, will be lost on restart)

## Troubleshooting

### State is lost after each deployment

**Problem**: Volume is not properly configured

**Solution**:
1. Check that the volume is created in Railway dashboard
2. Verify mount path is `/data`
3. Check logs to see where the state file is being saved

### Permission errors

**Problem**: Cannot write to `/data`

**Solution**:
1. Ensure the volume is properly mounted
2. Check Railway service logs for permission errors
3. Volume permissions should be automatically handled by Railway

## Benefits of Using Volumes

- ✅ State persists across deployments
- ✅ Restart-safe (no state loss)
- ✅ Efficient (only syncs changed files)
- ✅ Railway volumes are automatically backed up
