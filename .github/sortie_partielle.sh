#!/usr/bin/env bash
# Sortie partielle par le telephone (30 octobre 2026), sourcee par outils/marjane_nuit.sh
# et copiee dans outils/essai_sortie.workflow.yml.
#
# POURQUOI
# Avec un noeud de sortie Tailscale, TOUT le trafic du runner passe par le telephone,
# y compris son lien avec GitHub. La nuit du 30 octobre (01 h 59) et le releve relance
# le soir meme (19 h 44), le job marjane a ete annule sans auteur, sans journal
# (« The operation was canceled », blob de journal absent) : un runner qui perd GitHub
# par le telephone s'annule lui-meme. Marjane est servie par Cloudflare (2606:4700::/32) :
# seul le trafic vers Cloudflare passe desormais par le telephone (Marjane, la trace
# loc=MA de VERIF_SORTIE, Supabase) ; le reste, dont GitHub, sort en direct.
#
# COMMENT
# Regles ip aux priorites 5100 a 5190, avant celles de Tailscale (5210 a 5270) :
#   5100 paquets de tailscaled lui-meme (fwmark 0x80000) : table main, comme 5210 ;
#   5110 adresses du reseau Tailscale : table 52 (celle de Tailscale) ;
#   5120 plages Cloudflare publiees (cloudflare.com/ips-v4 et ips-v6) : table 52 ;
#   5190 tout le reste : table main, sortie directe du runner.
# tailscale set --exit-node ne touche que ses propres priorites : les regles restent
# quand remettre_sortie remet le noeud. Si une regle echoue, tout est retire et la
# sortie redevient totale, comme avant le 30 octobre.

PLAGES_CLOUDFLARE_V4="173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 104.24.0.0/14 172.64.0.0/13 131.0.72.0/22"
PLAGES_CLOUDFLARE_V6="2400:cb00::/32 2606:4700::/32 2803:f800::/32 2405:b500::/32 2405:8100::/32 2a06:98c0::/29 2c0f:f248::/32"

retirer_sortie_partielle () {
    local famille prio
    for famille in -4 -6; do
        for prio in 5100 5110 5120 5190; do
            while sudo ip "$famille" rule del priority "$prio" 2> /dev/null; do :; done
        done
    done
    return 0
}

poser_sortie_partielle () {
    # Rend 0 si toutes les regles sont posees, 1 sinon (et alors rien ne reste).
    local p
    retirer_sortie_partielle
    # Sous-shell : un « exit 1 » n'y quitte que lui, jamais le script qui source ce fichier.
    (
        sudo ip -4 rule add priority 5100 fwmark 0x80000/0xff0000 lookup main &&
        sudo ip -4 rule add priority 5110 to 100.64.0.0/10 lookup 52 &&
        for p in $PLAGES_CLOUDFLARE_V4; do
            sudo ip -4 rule add priority 5120 to "$p" lookup 52 || exit 1
        done &&
        sudo ip -4 rule add priority 5190 lookup main &&
        sudo ip -6 rule add priority 5100 fwmark 0x80000/0xff0000 lookup main &&
        sudo ip -6 rule add priority 5110 to fd7a:115c:a1e0::/48 lookup 52 &&
        for p in $PLAGES_CLOUDFLARE_V6; do
            sudo ip -6 rule add priority 5120 to "$p" lookup 52 || exit 1
        done &&
        sudo ip -6 rule add priority 5190 lookup main
    ) 2> /dev/null && return 0
    retirer_sortie_partielle
    return 1
}

voie_vers () {
    # voie_vers <adresse> : interface de sortie vue par le noyau (tailscale0, eth0...).
    ip route get "$1" 2> /dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -n 1
}
