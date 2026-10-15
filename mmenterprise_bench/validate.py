"""Validate the MMEnterprise-Bench dataset and its retrieval rankings (``mmeb validate``).

Checks file checksums, media references, question and record identifiers, gold evidence, task labels
and both retrieval rankings using only the standard library. ``.cache/`` (huggingface_hub download
metadata) is ignored.
"""

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys


TASKS = {
    'Fact': ('Evidence Reasoning', 'Extraction'),
    'Attribution': ('Evidence Reasoning', 'Attribution'),
    'Computation': ('Evidence Reasoning', 'Computation'),
    'Selection': ('Evidence Reasoning', 'Comparison'),
    'Interpretation': ('Evidence Reasoning', 'Interpretation'),
    'Discrepancy': ('Conflict Resolution', 'Cross-source'),
    'Resolution': ('Conflict Resolution', 'Cross-source'),
    'State': ('Conflict Resolution', 'Temporal'),
    'Change': ('Conflict Resolution', 'Temporal'),
    'Abstention': ('Unanswerability', 'Unanswerability'),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(root, name):
    return json.loads((root / name).read_text(encoding='utf-8'))


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def local_file(root, relative):
    require(isinstance(relative, str), 'A file path must be a string')
    parts = PurePosixPath(relative)
    require(not parts.is_absolute() and '..' not in parts.parts and '\\' not in relative,
            f'Invalid relative path: {relative}')
    path = root / relative
    require(path.is_file(), f'Missing file: {relative}')
    require(path.resolve().is_relative_to(root), f'File outside dataset: {relative}')
    return path


def keyed(values, name):
    result = {item['id']: item for item in values}
    require(len(result) == len(values), f'Duplicate {name} identifiers')
    return result


IGNORED_DIRS = {'.cache'}


def dataset_files(root):
    return [path for path in root.rglob('*') if not (set(path.relative_to(root).parts) & IGNORED_DIRS)]


def validate(root, check_checksums=True):
    root = Path(root).resolve()
    require(root.is_dir(), f'Dataset directory does not exist: {root}')
    require((root / 'qa/rows.json').is_file(), f'Not a dataset directory (no qa/rows.json): {root}')
    for path in dataset_files(root):
        require(not path.is_symlink(), f'Symlink in dataset: {path.relative_to(root)}')

    hashes = {}
    if check_checksums:
        for line in (root / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
            expected, relative = line.split('  ', 1)
            require(re.fullmatch(r'[0-9a-f]{64}', expected), f'Invalid checksum: {relative}')
            require(relative not in hashes, f'Duplicate checksum entry: {relative}')
            actual = file_hash(local_file(root, relative))
            require(actual == expected, f'Checksum mismatch: {relative}')
            hashes[relative] = actual
        files = {str(path.relative_to(root)) for path in dataset_files(root) if path.is_file()}
        require(set(hashes) == files - {'SHA256SUMS'}, 'Checksum file does not cover the complete dataset')

    manifest = load(root, 'manifest.json')
    questions = load(root, 'qa/rows.json')
    units = load(root, 'corpus/units.json')
    projects = load(root, 'projects.json')
    question_map = keyed(questions, 'question')
    unit_map = keyed(units, 'retrieval-unit')
    project_map = keyed(projects, 'project')
    record_ids = {unit['record_id'] for unit in units}
    require(len(questions) == manifest['questions'] == 1613, 'Expected 1,613 questions')
    require(len(units) == manifest['retrieval_records'] == 7140, 'Expected 7,140 retrieval records')
    require(len(projects) == manifest['projects'] == 30, 'Expected 30 projects')
    require(len(record_ids) == manifest['native_record_ids'], 'Native record count mismatch')

    media = set()
    for unit in units:
        require(unit['record_id'].split('::', 1)[0] in project_map,
                f"Unknown project for unit {unit['id']}")
        require(isinstance(unit['text'], str) and unit['text'], f"Empty text for unit {unit['id']}")
        require(isinstance(unit['order'], int), f"Invalid corpus order: {unit['id']}")
        if unit['image']:
            path = local_file(root, unit['image'])
            actual = hashes.get(unit['image']) or file_hash(path)
            require(actual == unit['image_sha256'], f"Image hash mismatch: {unit['id']}")
            media.add(unit['image'])
        else:
            require(unit['image_sha256'] is None, f"Unexpected image hash: {unit['id']}")
    require(len(media) == manifest['media_files'] == 1415, 'Expected 1,415 media files')
    require(sum((root / path).stat().st_size for path in media) == manifest['media_bytes'],
            'Media byte count mismatch')
    require(media == {str(path.relative_to(root)) for path in (root / 'media').rglob('*') if path.is_file()},
            'Media directory contains unreferenced files')

    modalities = Counter()
    for question in questions:
        qid = question['id']
        require(question['project'] in project_map, f'Unknown project: {qid}')
        require(isinstance(question['q'], str) and question['q'].strip(), f'Empty question: {qid}')
        require(isinstance(question['gold']['answer'], str) and question['gold']['answer'].strip(),
                f'Empty reference answer: {qid}')
        require((question['category'], question['task']) == TASKS[question['primary_task']],
                f'Task mapping mismatch: {qid}')
        require(question['answer_status'] in {'answerable', 'unanswerable'}, f'Invalid answer status: {qid}')
        require((question['answer_status'] == 'unanswerable') == (question['task'] == 'Unanswerability'),
                f'Task and answer status disagree: {qid}')
        reference_units = question['frozen_representative_retrieval_units']
        require(len(reference_units) == len(set(reference_units)), f'Duplicate gold unit: {qid}')
        require(all(uid in unit_map for uid in reference_units), f'Unknown gold unit: {qid}')
        selected = [unit_map[uid] for uid in reference_units]
        require(set(question['evidence']) == {unit['record_id'] for unit in selected},
                f'Gold record/unit mapping mismatch: {qid}')
        for path in question['evidence_paths']:
            require(path and all(rid in record_ids for rid in path), f'Unknown gold record: {qid}')
        for step in question['solution']:
            require(all(rid in record_ids for rid in step['record_ids']), f'Unknown solution record: {qid}')
        if question['answer_status'] == 'answerable':
            require(reference_units and question['evidence_paths'], f'Missing gold evidence: {qid}')
            require(question['evidence_paths'][0] == question['evidence'], f'Representative path mismatch: {qid}')
        else:
            require(not reference_units and not question['evidence_paths'], f'Unexpected unanswerable evidence: {qid}')
        image_count = sum(bool(unit['image']) for unit in selected)
        modality = None if not selected else 'text' if not image_count else 'image' if image_count == len(selected) else 'multi'
        require(question['evidence_modality'] == modality, f'Evidence modality mismatch: {qid}')
        require(question['evidence_image_count'] == image_count, f'Evidence image count mismatch: {qid}')
        require(question['evidence_unit_count'] == len(selected), f'Evidence unit count mismatch: {qid}')
        require(question['evidence_record_count'] == len(set(question['evidence'])), f'Evidence record count mismatch: {qid}')
        if modality:
            modalities[modality] += 1

    require(dict(modalities) == manifest['representative_evidence_modality'], 'Evidence modality totals mismatch')
    require(Counter(q['task'] for q in questions) == manifest['tasks'], 'Task totals mismatch')
    require(Counter(q['category'] for q in questions) == manifest['categories'], 'Category totals mismatch')
    status_counts = Counter(q['answer_status'] for q in questions)
    require(status_counts['answerable'] == manifest['answerable_questions'] == 1526, 'Answerable count mismatch')
    require(status_counts['unanswerable'] == manifest['unanswerable_questions'] == 87, 'Unanswerable count mismatch')
    require(sum(bool(unit['image']) for unit in units) == manifest['visual_records'], 'Visual record count mismatch')
    require(sum(not unit['image'] for unit in units) == manifest['text_records'], 'Text record count mismatch')
    question_counts = Counter(q['project'] for q in questions)
    unit_counts = Counter(unit['record_id'].split('::', 1)[0] for unit in units)
    for project in projects:
        require(project['questions'] == question_counts[project['id']], f"Project question count mismatch: {project['id']}")
        require(project['retrieval_records'] == unit_counts[project['id']], f"Project record count mismatch: {project['id']}")

    retrieval_config = load(root, 'retrieval/config.json')
    require(retrieval_config['top_k'] == 100, 'Invalid retrieval configuration depth')
    require(set(retrieval_config['models']) == {'2b', '8b'}, 'Invalid retriever configuration')
    for size in ('2b', '8b'):
        ranks = load(root, f'retrieval/ranks_{size}.json')
        require(ranks['retriever'] == retrieval_config['models'][size]['model_id'], f'Retriever model mismatch: {size}')
        require(re.fullmatch(r'[0-9a-f]{40}', retrieval_config['models'][size]['revision']), f'Invalid retriever revision: {size}')
        require(ranks['top_k'] == 100, f'Invalid retrieval depth: {size}')
        require(set(ranks['hits']) == set(question_map), f'Ranking question IDs mismatch: {size}')
        require(set(ranks['query_sha256']) == set(question_map), f'Query hash IDs mismatch: {size}')
        for qid, hits in ranks['hits'].items():
            query = json.dumps(question_map[qid]['q'], ensure_ascii=False).encode('utf-8')
            require(ranks['query_sha256'][qid] == hashlib.sha256(query).hexdigest(), f'Query hash mismatch: {size}/{qid}')
            require(len(hits) == 100 and len({hit['id'] for hit in hits}) == 100, f'Invalid hit count: {size}/{qid}')
            require([hit['rank'] for hit in hits] == list(range(1, 101)), f'Invalid rank sequence: {size}/{qid}')
            require(all(hit['id'] in unit_map for hit in hits), f'Unknown ranked unit: {size}/{qid}')
            for hit in hits:
                if 'score' in hit:
                    require(isinstance(hit['score'], (int, float)) and math.isfinite(hit['score']),
                            f'Invalid similarity score: {size}/{qid}')

    seeds = load(root, 'seed_documents.json')
    require(len(seeds) == len(projects) and {seed['project_id'] for seed in seeds} == set(project_map),
            'Source document/project mapping mismatch')
    return {
        'dataset': manifest['name'], 'version': manifest['version'], 'valid': True,
        'questions': len(questions), 'projects': len(projects), 'retrieval_records': len(units),
        'media_files': len(media), 'rankings': ['2b', '8b'], 'checksums_verified': check_checksums,
    }


def run(root, skip_checksums=False):
    try:
        print(json.dumps(validate(root, not skip_checksums), indent=2))
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f'Validation failed: {error}', file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    from .data import default_dataset_dir
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', '--dataset', dest='data_root', type=Path, default=None,
                        help='Dataset directory (default: $MMEB_DATA_DIR or data/MMEnterprise-Bench)')
    parser.add_argument('--skip-checksums', action='store_true', help='Validate structure without the release checksum manifest')
    args = parser.parse_args(argv)
    return run(args.data_root or default_dataset_dir(), args.skip_checksums)


if __name__ == '__main__':
    raise SystemExit(main())
