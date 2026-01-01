#!/usr/bin/env python3
"""
Seedbox Torrent Mover (Mixed Deluge Web/rTorrent)
Automatically moves torrents from fast seedbox (Deluge) to slow seedbox (rTorrent) based on:
- Age: >= 14 days old
- Storage trigger: When fast seedbox hits a % full
- Action: Transfer files, add to slow seedbox, remove from fast seedbox
"""

import os
import sys
import time
import json
import logging
import subprocess
import xmlrpc.client
import http.cookiejar
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse
import urllib.request
import urllib.error

# ============================================================================
# CONFIGURATION - EDIT THESE VALUES
# ============================================================================

# Slow Seedbox Configuration (rTorrent)
SLOW_SEEDBOX = {
    'host': 'hostname.seedboxhost.com',  # Your slow seedbox hostname
    'rpc_url': 'https://hostname.seedboxhost.com/YOUR_USERNAME/rutorrent/plugins/httprpc/action.php',
    'username': 'YOUR_USERNAME',                     # Your username
    'password': 'YOUR_PASSWORD',              # ruTorrent password
    'ssh_host': 'YOUR_USERNAME@hostname.seedboxhost.com',
    'data_path': '/home/YOUR_USERNAME/data/rTorrent',  # Where rTorrent stores files
}

# Fast Seedbox Configuration (Deluge Web UI)
FAST_SEEDBOX = {
    'host': '192.168.0.0',                    # Your fast seedbox IP
    'web_url': 'https://user.seedbox.domain/deluge/',  # Deluge Web UI URL
    'password': 'YOUR_PASSWORD',                 # Deluge Web UI password
    'ssh_host': 'user@192.168.0.0',     # SSH connection string
    'data_path': '/home/YOUR_USERNAME/downloads/deluge',  # Where Deluge stores files
    'session_path': '/home/YOUR_USERNAME/.config/deluge/state',  # .torrent file location
    'storage_threshold': 40  # Trigger when disk usage exceeds this %
}

# Thresholds
MIN_AGE_DAYS = 14
DRY_RUN = False

# Logging
LOG_FILE = os.path.join(os.path.dirname(__file__), 'seedbox_mover.log')
LOG_LEVEL = logging.INFO

# ============================================================================
# CODE
# ============================================================================

logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class DelugeWebClient:
    """Deluge Web UI API client"""
    
    def __init__(self, web_url, password):
        self.web_url = web_url.rstrip('/')
        self.password = password
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPSHandler(context=self._get_ssl_context())
        )
        self.request_id = 0
        self.connected = False
    
    def _get_ssl_context(self):
        """Create SSL context that doesn't verify certificates (for self-signed certs)"""
        import ssl
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    
    def _call(self, method, params=None):
        """Make JSON-RPC call to Deluge Web UI"""
        if params is None:
            params = []
        
        self.request_id += 1
        
        payload = {
            "method": method,
            "params": params,
            "id": self.request_id
        }
        
        url = f"{self.web_url}/json"
        
        try:
            data = json.dumps(payload).encode('utf-8')
            request = urllib.request.Request(
                url,
                data=data,
                headers={
                    'Content-Type': 'application/json',
                    'Accept-Encoding': 'gzip, deflate'
                }
            )
            
            response = self.opener.open(request, timeout=30)
            
            # Handle gzip compression
            response_data = response.read()
            if response.info().get('Content-Encoding') == 'gzip':
                import gzip
                response_data = gzip.decompress(response_data)
            
            result = json.loads(response_data.decode('utf-8'))
            
            if 'error' in result and result['error']:
                raise Exception(f"Deluge error: {result['error']}")
            
            return result.get('result')
            
        except urllib.error.HTTPError as e:
            logger.error(f"HTTP error calling {method}: {e.code} {e.reason}")
            raise
        except Exception as e:
            logger.error(f"Deluge Web API call failed ({method}): {e}")
            raise
    
    def connect(self):
        """Authenticate with Deluge Web UI"""
        try:
            # Login to web interface
            logger.info(f"Attempting to login to {self.web_url}")
            result = self._call("auth.login", [self.password])
            logger.info(f"Login result: {result}")
            
            if result:
                logger.info("Successfully authenticated with Deluge Web UI")
                
                # Get connected hosts
                logger.info("Getting available hosts...")
                hosts = self._call("web.get_hosts")
                logger.info(f"Available hosts: {hosts}")
                
                if hosts and len(hosts) > 0:
                    # Connect to first available host
                    host_id = hosts[0][0]
                    logger.info(f"Connecting to host: {host_id}")
                    connect_result = self._call("web.connect", [host_id])
                    logger.info(f"Connect result: {connect_result}")
                    logger.info(f"Connected to Deluge daemon via Web UI")
                    self.connected = True
                    return True
                else:
                    logger.error("No Deluge hosts available")
                    return False
            else:
                logger.error(f"Login failed - incorrect password?")
                return False
        except Exception as e:
            logger.error(f"Failed to connect to Deluge Web UI: {e}", exc_info=True)
            return False
    
    def get_torrents(self):
        """Get list of all torrents"""
        try:
            if not self.connected:
                self.connect()
            
            # Get all torrents with their status
            result = self._call("web.update_ui", [
                ["name", "save_path", "total_size", "time_added", 
                 "is_finished", "ratio", "state", "progress"],
                {}
            ])
            
            if result and 'torrents' in result:
                return result['torrents']
            return {}
        except Exception as e:
            logger.error(f"Failed to get torrents: {e}")
            return {}
    
    def get_torrent_status(self, hash_id, keys):
        """Get specific torrent status"""
        try:
            result = self._call("web.get_torrent_status", [hash_id, keys])
            return result
        except Exception as e:
            logger.error(f"Failed to get torrent status for {hash_id}: {e}")
            return None
    
    def remove_torrent(self, hash_id, remove_data=False):
        """Remove torrent from Deluge"""
        try:
            # Try newer API first
            try:
                result = self._call("web.remove_torrents", [[hash_id], remove_data])
                logger.info(f"Removed torrent from Deluge: {hash_id}")
                return True
            except:
                # Fall back to older API method
                result = self._call("core.remove_torrent", [hash_id, remove_data])
                logger.info(f"Removed torrent from Deluge: {hash_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to remove torrent {hash_id}: {e}")
            return False


class RTorrentClient:
    """rTorrent XML-RPC client"""
    
    def __init__(self, rpc_url, username, password):
        parsed = urlparse(rpc_url)
        netloc = f"{username}:{password}@{parsed.netloc}"
        auth_url = urlunparse((parsed.scheme, netloc, parsed.path, 
                              parsed.params, parsed.query, parsed.fragment))
        self.server = xmlrpc.client.ServerProxy(auth_url)
    
    def add_torrent(self, torrent_file_path, download_dir):
        """Add torrent to rTorrent"""
        try:
            with open(torrent_file_path, 'rb') as f:
                torrent_data = xmlrpc.client.Binary(f.read())
            
            self.server.load.raw_start('', torrent_data, f'd.directory.set="{download_dir}"')
            logger.info(f"Added torrent to rTorrent: {os.path.basename(torrent_file_path)}")
            return True
        except Exception as e:
            logger.error(f"Failed to add torrent to rTorrent: {e}")
            return False


def check_disk_usage(ssh_host, path):
    """Check disk usage percentage on remote host"""
    try:
        cmd = f"ssh {ssh_host} 'df -h {path} | tail -1'"
        result = subprocess.check_output(cmd, shell=True, text=True)
        parts = result.split()
        usage_str = parts[4].rstrip('%')
        usage_pct = int(usage_str)
        logger.info(f"Disk usage on {ssh_host}:{path} = {usage_pct}%")
        return usage_pct
    except Exception as e:
        logger.error(f"Failed to check disk usage on {ssh_host}: {e}")
        return 0


def get_torrent_file(ssh_host, hash_id, session_path):
    """Download .torrent file from remote seedbox"""
    try:
        local_path = f"/tmp/{hash_id}.torrent"
        
        # Try common locations for .torrent files
        possible_paths = [
            f"{session_path}/{hash_id}.torrent",
            f"/home/YOUR_USERNAME/.config/deluge/state/{hash_id}.torrent",
            f"/home/YOUR_USERNAME/session/{hash_id}.torrent",
        ]
        
        for remote_path in possible_paths:
            cmd = f"scp {ssh_host}:{remote_path} {local_path} 2>/dev/null"
            result = subprocess.run(cmd, shell=True, capture_output=True)
            
            if result.returncode == 0 and os.path.exists(local_path):
                logger.info(f"Downloaded .torrent file from: {remote_path}")
                return local_path
        
        logger.error(f"Could not find .torrent file for {hash_id} in any location")
        return None
    except Exception as e:
        logger.error(f"Failed to get .torrent file for {hash_id}: {e}")
        return None


def rsync_files(src_ssh, src_path, dst_ssh, dst_path):
    """Rsync files from fast seedbox to slow seedbox via localhost"""
    try:
        # Create temp directory on localhost
        temp_dir = os.path.expanduser("~/seedbox_temp")
        os.makedirs(temp_dir, exist_ok=True)
        
        basename = os.path.basename(src_path)
        local_temp_path = os.path.join(temp_dir, basename)
        
        # Step 1: Pull from source to localhost
        logger.info(f"Pulling from fast seedbox: {src_ssh}:{src_path}")
        cmd1 = [
            'rsync',
            '-avz',
            '--progress',
            f'{src_ssh}:{src_path}',
            temp_dir
        ]
        
        result1 = subprocess.run(cmd1, capture_output=True, text=True)
        
        if result1.returncode != 0:
            logger.error(f"Pull from fast seedbox failed: {result1.stderr}")
            return False
        
        logger.info(f"Successfully pulled to localhost: {local_temp_path}")
        
        # Step 2: Push from localhost to destination
        logger.info(f"Pushing to slow seedbox: {dst_ssh}:{dst_path}")
        cmd2 = [
            'rsync',
            '-avz',
            '--progress',
            local_temp_path,
            f'{dst_ssh}:{dst_path}'
        ]
        
        result2 = subprocess.run(cmd2, capture_output=True, text=True)
        
        if result2.returncode != 0:
            logger.error(f"Push to slow seedbox failed: {result2.stderr}")
            return False
        
        logger.info(f"Successfully pushed to slow seedbox")
        
        # Cleanup temp file
        try:
            if os.path.isfile(local_temp_path):
                os.remove(local_temp_path)
            elif os.path.isdir(local_temp_path):
                import shutil
                shutil.rmtree(local_temp_path)
        except Exception as e:
            logger.warning(f"Failed to cleanup temp file: {e}")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to rsync: {e}")
        return False


def delete_remote_files(ssh_host, path):
    """Delete files on remote host"""
    try:
        cmd = f"ssh {ssh_host} 'rm -rf \"{path}\"'"
        subprocess.check_call(cmd, shell=True)
        logger.info(f"Deleted files on {ssh_host}:{path}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete {ssh_host}:{path}: {e}")
        return False


def get_torrents_to_move(deluge_client, min_age_days):
    """Get list of torrents that are old enough to move"""
    torrents_to_move = []
    
    torrents = deluge_client.get_torrents()
    logger.info(f"Found {len(torrents)} torrents on fast seedbox")
    
    cutoff_time = int((datetime.now() - timedelta(days=min_age_days)).timestamp())
    
    for hash_id, torrent in torrents.items():
        if not torrent.get('is_finished'):
            continue
        
        time_added = torrent.get('time_added', 0)
        if time_added <= cutoff_time:
            age_days = (datetime.now().timestamp() - time_added) / 86400
            size_gb = torrent.get('total_size', 0) / (1024**3)
            ratio = torrent.get('ratio', 0)
            name = torrent.get('name', 'Unknown')
            
            logger.info(f"  Candidate: {name} (age: {age_days:.1f} days, ratio: {ratio:.2f}, size: {size_gb:.2f} GB)")
            
            torrents_to_move.append({
                'hash': hash_id,
                'name': name,
                'path': torrent.get('save_path', ''),
                'size': torrent.get('total_size', 0),
                'created': time_added,
                'ratio': ratio
            })
    
    torrents_to_move.sort(key=lambda x: x['created'])
    return torrents_to_move


def move_torrent(torrent_info, fast_cfg, slow_cfg):
    """Move a single torrent from Deluge to rTorrent"""
    hash_id = torrent_info['hash']
    name = torrent_info['name']
    
    logger.info(f"=== Moving torrent: {name} ===")
    
    # Step 1: Get .torrent file
    torrent_file = get_torrent_file(
        fast_cfg['ssh_host'],
        hash_id,
        fast_cfg['session_path']
    )
    if not torrent_file:
        logger.error(f"Failed to get .torrent file for {name}")
        return False
    
    # Step 2: Construct full path
    full_src_path = os.path.join(torrent_info['path'], name)
    
    # Step 3: Rsync files
    if not rsync_files(
        fast_cfg['ssh_host'], full_src_path,
        slow_cfg['ssh_host'], slow_cfg['data_path']
    ):
        logger.error(f"Failed to sync files for {name}")
        return False
    
    # Step 4: Add to rTorrent
    slow_client = RTorrentClient(
        slow_cfg['rpc_url'],
        slow_cfg['username'],
        slow_cfg['password']
    )
    
    if not slow_client.add_torrent(torrent_file, slow_cfg['data_path']):
        logger.error(f"Failed to add torrent to slow seedbox: {name}")
        return False
    
    # Step 5: Remove from Deluge
    fast_client = DelugeWebClient(
        fast_cfg['web_url'],
        fast_cfg['password']
    )
    
    if not fast_client.connect():
        logger.error("Failed to connect to Deluge Web UI for removal")
        return False
    
    if not fast_client.remove_torrent(hash_id, remove_data=False):
        logger.error(f"Failed to remove torrent from Deluge: {name}")
        return False
    
    # Step 6: Delete files
    if not delete_remote_files(fast_cfg['ssh_host'], full_src_path):
        logger.error(f"Failed to delete files from fast seedbox: {name}")
        return False
    
    try:
        os.remove(torrent_file)
    except:
        pass
    
    logger.info(f"✓ Successfully moved: {name}")
    return True


def main():
    """Main execution"""
    logger.info("=" * 60)
    logger.info("Starting Seedbox Torrent Mover (Deluge Web → rTorrent)")
    logger.info(f"Dry run mode: {DRY_RUN}")
    logger.info("=" * 60)
    
    usage = check_disk_usage(
        FAST_SEEDBOX['ssh_host'],
        FAST_SEEDBOX['data_path']
    )
    
    if usage < FAST_SEEDBOX['storage_threshold']:
        logger.info(f"Fast seedbox usage ({usage}%) below threshold ({FAST_SEEDBOX['storage_threshold']}%)")
        logger.info("No action needed")
        return
    
    logger.warning(f"Fast seedbox usage ({usage}%) exceeds threshold ({FAST_SEEDBOX['storage_threshold']}%)")
    logger.info("Looking for torrents to move...")
    
    deluge_client = DelugeWebClient(
        FAST_SEEDBOX['web_url'],
        FAST_SEEDBOX['password']
    )
    
    if not deluge_client.connect():
        logger.error("Failed to connect to Deluge Web UI")
        return
    
    torrents = get_torrents_to_move(deluge_client, MIN_AGE_DAYS)
    
    if not torrents:
        logger.info("No torrents found that meet criteria (age >= 14 days, completed)")
        return
    
    logger.info(f"Found {len(torrents)} torrents eligible for moving")
    
    if DRY_RUN:
        logger.warning("DRY RUN MODE - No actual changes will be made")
        for t in torrents[:5]:
            age_days = (datetime.now().timestamp() - t['created']) / 86400
            size_gb = t['size'] / (1024**3)
            logger.info(f"  Would move: {t['name']} ({size_gb:.2f} GB, {age_days:.1f} days old, ratio: {t['ratio']:.2f})")
        if len(torrents) > 5:
            logger.info(f"  ... and {len(torrents) - 5} more")
        return
    
    moved_count = 0
    for torrent in torrents:
        if move_torrent(torrent, FAST_SEEDBOX, SLOW_SEEDBOX):
            moved_count += 1
            
            current_usage = check_disk_usage(
                FAST_SEEDBOX['ssh_host'],
                FAST_SEEDBOX['data_path']
            )
            
            if current_usage < FAST_SEEDBOX['storage_threshold']:
                logger.info(f"✓ Storage now at {current_usage}% - below threshold")
                break
        
        time.sleep(2)
    
    logger.info("=" * 60)
    logger.info(f"Moved {moved_count} torrents")
    logger.info("Seedbox Torrent Mover Complete")
    logger.info("=" * 60)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)