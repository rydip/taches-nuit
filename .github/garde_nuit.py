"""Autorise les relevés entre minuit et 03 h au Maroc, au plus deux essais.

Copie publique : .github/garde_nuit.py du dépôt d'exécution. Bibliothèque
standard seulement ; aucune lecture d'une enseigne ou de la base.
Les notifications et relevés restent dans les scripts du dépôt privé.

Quatre comportements selon l'heure de départ (Maroc) :
- 20 h à 23 h 59 : armement. Si aucune autre exécution du workflow n'est armée
  ou en cours, cette exécution dort jusqu'à 00 h 02, puis décide à l'heure réelle.
- 00 h à 02 h 59 : décision normale (choisir), relevés seulement dans la fenêtre.
- 03 h à 11 h 59 : contrôle du matin sur la nuit qui vient de finir. Aucun relevé
  n'est lancé ; une issue est ouverte si aucun relevé n'a réussi pour un job.
- 12 h à 19 h 59 : hors nuit, rien à faire.
Mode --bilan : ouvre une issue pour chaque job de relevé en échec (variable NEEDS).
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

MAROC = ZoneInfo('Africa/Casablanca')
MAX_ESSAIS = 2
HEURE_ARMEMENT = range(20, 24)  # 20 h 00 à 23 h 59, heure du Maroc
MATIN = range(3, 12)            # 03 h 00 à 11 h 59, heure du Maroc
PAUSE_MAX = 600                 # secondes entre deux réveils de contrôle


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


def ecrire_api(chemin, donnees):
    req = Request('https://api.github.com/' + chemin,
                  data=json.dumps(donnees).encode('utf-8'), method='POST', headers={
                      'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                      'Accept': 'application/vnd.github+json',
                      'Content-Type': 'application/json',
                  })
    with urlopen(req, timeout=30) as rep:
        return json.load(rep)


def lire_runs(maintenant, workflow, lire=lire_api):
    """Exécutions du workflow créées depuis la veille de la nuit, courante incluse."""
    repo = os.environ['GITHUB_REPOSITORY']
    # Un déclenchement retardé peut avoir été créé la veille. Il faut le voir.
    depuis = (debut_nuit(maintenant) - timedelta(days=1)).astimezone(timezone.utc).isoformat()
    prefixe = f'repos/{repo}/actions/'
    runs = []
    for page in range(1, 11):
        params = urlencode({'per_page': 100, 'page': page, 'created': '>=' + depuis})
        lot = lire(prefixe + f'workflows/{workflow}/runs?' + params)['workflow_runs']
        runs.extend(lot)
        if len(lot) < 100:
            return runs
    raise RuntimeError('Historique de runs trop volumineux : aucun relevé autorisé')


def lire_jobs(run_id, lire=lire_api):
    repo = os.environ['GITHUB_REPOSITORY']
    prefixe = f'repos/{repo}/actions/'
    jobs = []
    # filter=all garde les tentatives des réexécutions, chacune compte.
    for page in range(1, 11):
        lot = lire(prefixe + f'runs/{run_id}/jobs?filter=all&per_page=100&page={page}')['jobs']
        jobs.extend(lot)
        if len(lot) < 100:
            return jobs
    raise RuntimeError('Historique de jobs trop volumineux : aucun relevé autorisé')


def lire_historique(maintenant, workflow, lire=lire_api):
    courant = int(os.environ['GITHUB_RUN_ID'])
    jobs = []
    for run in lire_runs(maintenant, workflow, lire):
        if run['id'] != courant:
            jobs.extend(lire_jobs(run['id'], lire))
    return jobs


def releve_reussi(job):
    """Vrai seulement si l'étape réelle `run` a réussi, pas un contrôle à vide."""
    releve = next((s for s in job.get('steps', []) if s['name'] == 'run'), None)
    return job.get('conclusion') == 'success' and releve is not None and releve.get('conclusion') == 'success'


def armer(maintenant, workflow, lire=lire_api):
    """Vrai si cette exécution doit dormir jusqu'à 00 h 02 : départ entre 20 h et 23 h 59,
    et aucune autre exécution du workflow n'est déjà armée ou en cours."""
    if maintenant.astimezone(MAROC).hour not in HEURE_ARMEMENT:
        return False
    courant = int(os.environ['GITHUB_RUN_ID'])
    autres = [r for r in lire_runs(maintenant, workflow, lire) if r['id'] != courant]
    return not any(r['status'] == 'in_progress' for r in autres)


def reveil(maintenant):
    """00 h 02, heure du Maroc, le lendemain du départ armé."""
    jour = maintenant.astimezone(MAROC).date() + timedelta(days=1)
    return datetime(jour.year, jour.month, jour.day, 0, 2, tzinfo=MAROC).astimezone(timezone.utc)


def maintenant_utc():
    return datetime.now(timezone.utc)


def attendre_minuit(maintenant, sommeil=time.sleep, horloge=maintenant_utc):
    """Dort par tranches de dix minutes, en revérifiant l'horloge à chaque tranche."""
    cible = reveil(maintenant)
    restant = (cible - horloge()).total_seconds()
    while restant > 0:
        print(f'armée : réveil à 00 h 02 (Maroc), encore {int(restant // 60)} min')
        sommeil(min(restant, PAUSE_MAX))
        restant = (cible - horloge()).total_seconds()


def titres_ouverts(lire=lire_api):
    repo = os.environ['GITHUB_REPOSITORY']
    titres = set()
    for page in range(1, 11):
        lot = lire(f'repos/{repo}/issues?state=open&per_page=100&page={page}')
        titres.update(i['title'] for i in lot if 'pull_request' not in i)
        if len(lot) < 100:
            return titres
    raise RuntimeError('Trop d\'issues ouvertes pour vérifier les doublons : aucune issue ouverte')


def ouvrir_issues(propositions, lire=lire_api, ecrire=ecrire_api):
    """Ouvre les issues dont le titre n'existe pas déjà parmi les issues ouvertes."""
    if not propositions:
        return []
    repo = os.environ['GITHUB_REPOSITORY']
    deja = titres_ouverts(lire)
    creees = []
    for titre, corps in propositions:
        if titre in deja:
            continue
        ecrire(f'repos/{repo}/issues', {'title': titre, 'body': corps})
        deja.add(titre)
        creees.append(titre)
    return creees


def lien_run():
    serveur = os.environ.get('GITHUB_SERVER_URL', 'https://github.com')
    return f"{serveur}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"


def controle_matin(maintenant, workflow, noms, lire=lire_api, ecrire=ecrire_api):
    """Nuit qui vient de finir : un relevé réussi par job, sinon une issue.
    Rien n'est jugé tant qu'une exécution de la nuit n'est pas terminée."""
    local = maintenant.astimezone(MAROC)
    nuit = local.date().isoformat()
    courant = int(os.environ['GITHUB_RUN_ID'])
    # Relevés de la nuit : créés avant 03 h 00 locales, veille de la nuit comprise.
    fin_fenetre = datetime(local.year, local.month, local.day, 3, 0, tzinfo=MAROC).astimezone(timezone.utc)
    de_la_nuit = []
    for run in lire_runs(maintenant, workflow, lire):
        if run['id'] == courant:
            continue
        creee = datetime.fromisoformat(run['created_at'].replace('Z', '+00:00'))
        if creee < fin_fenetre:
            de_la_nuit.append(run)
    if any(r['status'] != 'completed' for r in de_la_nuit):
        return []
    reussis = set()
    for run in de_la_nuit:
        for job in lire_jobs(run['id'], lire):
            if job['name'] in noms and releve_reussi(job):
                reussis.add(job['name'])
    label = workflow.removesuffix('.yml')
    propositions = [
        (f'Nuit du {nuit} : aucun relevé réussi pour {label}/{nom}',
         f'Aucun relevé de la nuit du {nuit} n\'a réussi pour `{label}/{nom}` '
         f'(00 h–03 h, Maroc). Contrôle du matin : {lien_run()}')
        for nom in noms if nom not in reussis
    ]
    return ouvrir_issues(propositions, lire, ecrire)


def bilan(maintenant, workflow, needs, lire=lire_api, ecrire=ecrire_api):
    """Une issue par job de relevé dont l'étape a réellement échoué."""
    nuit = maintenant.astimezone(MAROC).date().isoformat()
    label = workflow.removesuffix('.yml')
    propositions = [
        (f'Nuit du {nuit} : {label}/{nom} en échec',
         f'Le relevé `{label}/{nom}` a échoué pendant la nuit du {nuit}. '
         f'Exécution : {lien_run()}')
        for nom, etat in needs.items() if etat.get('result') == 'failure'
    ]
    return ouvrir_issues(propositions, lire, ecrire)


def planifier(workflow, noms, lire=lire_api, sommeil=time.sleep,
              horloge=maintenant_utc, ecrire=ecrire_api):
    """Décide pour chaque job de relevé. Retourne (décisions, issues ouvertes)."""
    maintenant = horloge()
    if armer(maintenant, workflow, lire):
        attendre_minuit(maintenant, sommeil, horloge)
        maintenant = horloge()
    if maintenant.astimezone(MAROC).hour in MATIN:
        issues = controle_matin(maintenant, workflow, noms, lire, ecrire)
        decisions = {nom: (False, 'matin : contrôle de la nuit précédente, aucun relevé lancé')
                     for nom in noms}
        return decisions, issues
    historiques = lire_historique(maintenant, workflow, lire) if dans_la_nuit(maintenant, maintenant) else []
    return choisir(maintenant, noms, historiques), []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workflow', required=True)
    parser.add_argument('--jobs', nargs='+', required=True)
    parser.add_argument('--bilan', action='store_true',
                        help='ouvre une issue pour chaque job en échec (JSON dans NEEDS)')
    args = parser.parse_args()
    if args.bilan:
        needs = json.loads(os.environ.get('NEEDS') or '{}')
        for titre in bilan(maintenant_utc(), args.workflow, needs):
            print(f'issue ouverte : {titre}')
        return
    decisions, issues = planifier(args.workflow, args.jobs)
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as sortie:
        for nom, (autorise, raison) in decisions.items():
            sortie.write(f'{nom}={str(autorise).lower()}\n')
            print(f'{nom} : {raison}')
    for titre in issues:
        print(f'issue ouverte : {titre}')
    # Un contrôle sans relevé est un succès, mais n'a aucune étape run :
    # il ne peut jamais être confondu avec une nuit réellement réussie.


if __name__ == '__main__':
    main()
