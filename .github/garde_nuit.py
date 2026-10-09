"""Autorise les relevés entre minuit et 03 h au Maroc, au plus deux essais.

Copie publique : .github/garde_nuit.py du dépôt d'exécution. Bibliothèque
standard seulement ; aucune lecture d'une enseigne ou de la base.
Les notifications et relevés restent dans les scripts du dépôt privé.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

MAROC = ZoneInfo('Africa/Casablanca')
MAX_ESSAIS = 2


def debut_nuit(maintenant):
    local = maintenant.astimezone(MAROC)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def dans_la_nuit(date, maintenant):
    local = date.astimezone(MAROC)
    return local.date() == maintenant.astimezone(MAROC).date() and local.hour < 3


def choisir(maintenant, noms, historiques):
    """Historique des étapes réelles, jamais le succès d'un contrôle à vide."""
    decisions = {}
    for nom in noms:
        if not dans_la_nuit(maintenant, maintenant):
            decisions[nom] = (False, 'hors nuit (00 h–03 h, Maroc)')
            continue
        essais = []
        for job in historiques:
            if job['name'] != nom:
                continue
            releve = next((s for s in job.get('steps', []) if s['name'] == 'run'), None)
            if not releve or not releve.get('started_at') or releve['conclusion'] == 'skipped':
                continue
            date = datetime.fromisoformat(releve['started_at'].replace('Z', '+00:00'))
            if dans_la_nuit(date, maintenant):
                essais.append((job, releve))
        if any(j['status'] != 'completed' for j, _ in essais):
            decisions[nom] = (False, 'relevé déjà en cours')
        elif any(j['conclusion'] == 'success' and s['conclusion'] == 'success' for j, s in essais):
            decisions[nom] = (False, 'déjà réussi cette nuit')
        elif len(essais) >= MAX_ESSAIS:
            decisions[nom] = (False, 'deux tentatives effectuées, arrêt pour cette nuit')
        else:
            decisions[nom] = (True, f'tentative {len(essais) + 1}/{MAX_ESSAIS}')
    return decisions


def lire_api(chemin):
    req = Request('https://api.github.com/' + chemin, headers={
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
        'Accept': 'application/vnd.github+json',
    })
    with urlopen(req, timeout=30) as rep:
        return json.load(rep)


def lire_historique(maintenant, workflow, lire=lire_api):
    repo, courant = os.environ['GITHUB_REPOSITORY'], int(os.environ['GITHUB_RUN_ID'])
    # Un déclenchement retardé peut avoir été créé la veille. Il faut le voir.
    depuis = (debut_nuit(maintenant) - timedelta(days=1)).astimezone(timezone.utc).isoformat()
    prefixe = f'repos/{repo}/actions/'
    jobs = []
    for page in range(1, 11):
        params = urlencode({'per_page': 100, 'page': page, 'created': '>=' + depuis})
        runs = lire(prefixe + f'workflows/{workflow}/runs?' + params)['workflow_runs']
        for run in runs:
            if run['id'] == courant:
                continue
            # filter=all garde les tentatives des réexécutions, chacune compte.
            for page_jobs in range(1, 11):
                lot = lire(prefixe + f"runs/{run['id']}/jobs?filter=all&per_page=100&page={page_jobs}")['jobs']
                jobs.extend(lot)
                if len(lot) < 100:
                    break
            else:
                raise RuntimeError('Historique de jobs trop volumineux : aucun relevé autorisé')
        if len(runs) < 100:
            return jobs
    raise RuntimeError('Historique de runs trop volumineux : aucun relevé autorisé')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workflow', required=True)
    parser.add_argument('--jobs', nargs='+', required=True)
    args = parser.parse_args()
    maintenant = datetime.now(timezone.utc)
    historiques = lire_historique(maintenant, args.workflow) if dans_la_nuit(maintenant, maintenant) else []
    decisions = choisir(maintenant, args.jobs, historiques)
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as sortie:
        for nom, (autorise, raison) in decisions.items():
            sortie.write(f'{nom}={str(autorise).lower()}\n')
            print(f'{nom} : {raison}')
    # Un contrôle sans relevé est un succès, mais n'a aucune étape run :
    # il ne peut jamais être confondu avec une nuit réellement réussie.


if __name__ == '__main__':
    main()
