interfaces {
    ethernet eth0 {
        address "172.20.20.11/24"
        description "mgmt"
    }
    loopback lo {
    }
}
service {
    ssh {
        listen-address "0.0.0.0"
        port "22"
    }
}
system {
    config-management {
        commit-revisions "100"
    }
    host-name "fw1"
    login {
        user admin {
            authentication {
                plaintext-password "demo-vyos-password"
            }
        }
    }
}
firewall {
    ipv4 {
        name ALLOW-WEB {
            default-action "drop"
            rule 1 {
                action "accept"
                description "seed rule — replaced on first deploy"
                destination {
                    port "443"
                }
                protocol "tcp"
            }
        }
    }
}
