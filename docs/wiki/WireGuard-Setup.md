# WireGuard Tunnel — Encrypted Node Transit

## Why

cAIc cluster traffic is plaintext today:

| Traffic | Protocol | Plaintext risk |
|---------|----------|----------------|
| AMQP (coordinator ↔ worker agent) | TCP :5672 | Registration, ping/pong, swap commands |
| Inference (coordinator → worker llama-server) | HTTP :8081 | Every token generated |
| LLM RPC layer offload (coordinator llama-server → worker) | TCP :50052 | Internal llama.cpp protocol |

WireGuard encrypts all three at the network layer with zero application changes. The cAIc app keeps using `http://` URLs — it's just talking to a virtual IP whose traffic is automatically encrypted before it hits the wire.

## Topology

```
┌───────────────────────┐      WireGuard tunnel       ┌───────────────────────┐
│  Coordinator (ultron)  │◄═══════════════════════════►│  Worker (jarvis)      │
│  10.0.2.1              │         UDP :51820          │  10.0.2.2              │
│  LAN 192.168.50.108    │                             │  LAN 192.168.50.210    │
│                        │════════════════════════════►│                        │
│                        │         UDP :51820          │  Worker (corsair)      │
│                        │                             │  10.0.2.3              │
│                        │                             │  LAN (DHCP)            │
└───────────────────────┘                             └───────────────────────┘
```

All nodes connect directly to the coordinator's WireGuard endpoint (star topology). Workers do not need to talk to each other.

## Prerequisites

```bash
# Debian / Ubuntu
sudo apt install wireguard

# Windows / WSL2 — install WireGuard from https://www.wireguard.com/install/
# The wg.exe binary is used inside WSL2; the Windows GUI manages the tunnel config
```

## Key Generation

Run once per node. Save the private key securely; public keys go into peer configs on the other end.

```bash
wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
chmod 600 /etc/wireguard/private.key
```

**Recorded keys for this deployment:**

| Node | Private key | Public key |
|------|-------------|------------|
| ultron | (node private) | `ultron_pubkey=` |
| jarvis | (node private) | `jarvis_pubkey=` |
| corsair | (node private) | `corsair_pubkey=` |

## Per-Node Configs

### Coordinator — `/etc/wireguard/wg0.conf` on ultron

```ini
[Interface]
Address = 10.0.2.1/24
ListenPort = 51820
PrivateKey = <ultron_private_key>

# Enable IP forwarding so workers can route through coordinator if needed
# sudo sysctl -w net.ipv4.ip_forward=1
# sudo sysctl -w net.ipv6.conf.all.forwarding=1

# Worker: jarvis
[Peer]
PublicKey = <jarvis_pubkey>
AllowedIPs = 10.0.2.2/32
# If jarvis is off-site, put its public IP / DDNS hostname here:
# Endpoint = jarvis.example.com:51820
# If jarvis is LAN-only, set PersistentKeepalive = 25 to maintain NAT binding:
# PersistentKeepalive = 25

# Worker: corsair
[Peer]
PublicKey = <corsair_pubkey>
AllowedIPs = 10.0.2.3/32
```

### Worker — `/etc/wireguard/wg0.conf` on jarvis (Linux)

```ini
[Interface]
Address = 10.0.2.2/24
ListenPort = 51820
PrivateKey = <jarvis_private_key>

# Coordinator
[Peer]
PublicKey = <ultron_pubkey>
AllowedIPs = 10.0.2.0/24
# LAN-only: just point at the LAN IP
Endpoint = 192.168.50.108:51820
# Off-site: use DDNS or static IP:
# Endpoint = ultron.example.com:51820
PersistentKeepalive = 25
```

### Worker — Windows / WSL2 on corsair

Create a WireGuard tunnel in the Windows GUI app with the same config as jarvis above (Address=10.0.2.3/24). WSL2 inside Windows can reach the tunnel IP via the Windows host.

If llama-server runs inside WSL2 on corsair, the Windows host's WireGuard tunnel IP `10.0.2.3` is reachable from the WSL2 instance as well — just bind llama-server to `0.0.0.0` (already the default) and configure the Windows firewall to allow inbound on :8081 from the coordinator's WireGuard IP.

## Starting the Tunnel

```bash
# Start immediately
sudo systemctl start wg-quick@wg0

# Enable on boot
sudo systemctl enable wg-quick@wg0

# Check status
sudo wg show
```

Expected output on each node:

```
interface: wg0
  public key: <...>
  private key: (hidden)
  listening port: 51820

peer: <ultron_pubkey>
  endpoint: 192.168.50.108:51820
  allowed ips: 10.0.2.0/24
  latest handshake: 5 seconds ago  ← healthy
  transfer: 1.2 KiB received, 3.4 KiB sent
```

If `latest handshake` is missing, check firewall rules (UDP :51820 must be open on all nodes).

## Verification

```bash
# From any node, ping another node's WireGuard IP
ping -c 3 10.0.2.1   # coordinator
ping -c 3 10.0.2.2   # jarvis
ping -c 3 10.0.2.3   # corsair

# Verify cAIc inference through the tunnel
curl http://10.0.2.2:8081/v1/models   # jarvis llama-server
curl http://10.0.2.3:8081/v1/models   # corsair llama-server
```

## Updating cAIc to Use the Tunnel

Once WireGuard is running, point each service at the tunnel IP instead of the LAN IP.

### Worker node agent config — `/etc/caic-node-agent.conf`

```ini
[agent]
node_name = jarvis
node_ip = 10.0.2.2           # was 192.168.50.210
node_type = worker
capabilities = llm
amqp_url = amqp://caic:password@10.0.2.1:5672/caic  # was 192.168.50.108
llama_port = 8081
models_dir = /var/lib/caic/models
active_model = qwen2.5-7b-instruct-Q5_K_M.gguf
```

### Coordinator config — environment variables

```bash
# On the coordinator node, override the worker-facing addresses
# (LLAMA_SERVER_BASE stays as localhost / LAN IP since inference
#  to the coordinator's own llama-server stays on-machine)
export CAIC_AMQP_URL="amqp://caic:password@10.0.2.1:5672/caic"
```

No other cAIc code changes are needed. The app already reads `CAIC_AMQP_URL` from the environment (`config.py:27`) and the node agent reads `node_ip` from its INI file. Inference requests routed to remote workers via `triage.py` use the IP the worker registered — so setting `node_ip = 10.0.2.2` in the worker's agent config is all it takes.

## Cross-Site Deployment Checklist

When placing a worker outside the LAN:

1. **Firewall:** Open UDP :51820 on the remote site. On the coordinator side, make sure UDP :51820 is reachable from the internet (port forward / firewall rule at the coordinator's router).
2. **DDNS:** If the coordinator's public IP is dynamic, set up a DDNS hostname and use it in the worker's `Endpoint = ultron.example.com:51820`.
3. **PersistentKeepalive:** Set `PersistentKeepalive = 25` on the worker side to keep NAT bindings alive.
4. **No double encryption:** WireGuard encrypts everything on the WireGuard interface. The cAIc app continues to use `http://` — it never touches raw TLS. This is correct and intended.
5. **Split tunnelling (optional):** The worker's `AllowedIPs = 10.0.2.0/24` ensures only cluster traffic goes through the tunnel. All other internet traffic from the worker uses its normal gateway.
