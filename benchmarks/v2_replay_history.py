import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from weather_optimizer import WeatherSeries
from src.daily_planning.evaluation import evaluate_baseline
root=pathlib.Path('results/source-daywork-v2')
w=WeatherSeries.from_csv(pathlib.Path('input/weather_for_optimizer_30min.csv'))
for folder in sorted(root.iterdir()):
    if not (folder/'input_snapshot.json').exists(): continue
    snap=json.loads((folder/'input_snapshot.json').read_text())
    for label in 'ABC':
        old=json.loads((folder/f'candidate_{label}_evaluation.json').read_text())
        planned=[r for r in old['vessel_results'] if r['assignment_kind']=='PLANNED']
        new=evaluate_baseline(snap,planned,w)['evaluation']
        print(folder.name,label,'old_cost',old['summary']['demurrage_increment_to_completion_cents'],'new_cost',new['summary']['demurrage_increment_to_completion_cents'],'rows_equal',old['vessel_results']==new['vessel_results'])
