import json
import shutil
import sys
from pathlib import Path
from typing import Any, TypeAlias, TypedDict

from export_utils import FolderTree, precompute_structures, serialize_recursive

# =====================================================================
#  Configuration
# =====================================================================
PATH_CURRENT_DIR = Path(__file__).parent
PATH_DEA_DB_DIR = PATH_CURRENT_DIR.parent
PATH_databases_to_export = PATH_DEA_DB_DIR / "event_groups"
PATH_exported = PATH_CURRENT_DIR / "exports"

DB_FILE_ENCODING = "utf-8"
STATIC_JSON_INDENT: int | None = None # None to remove indent, int otherwise (e.g. 4)

CIRCLE_INDEX_CIRCLES_PER_FILE = 10_000 # 10k circles per file
CIRCLE_COMPACT_INDEX_CIRCLES_PER_FILE = 40_000 # 40k circles per file

# =====================================================================
#  Event List Export - Data Structures
# =====================================================================

class EventListEventEntry(TypedDict):
    """Entry details for a single event in the event list index."""
    index: int
    dates: str
    circle_count: int | None

class EventListGroupEntry(TypedDict, total=False):
    """Entry details for an event group database in the event list index."""
    aliases: list[str]
    events: dict[str, EventListEventEntry]
    last_edited: str

# Defined as TypeAlias of dict[str, Any] to accommodate dynamic subfolder keys
EventListIndexNode: TypeAlias = dict[str, Any]

# =====================================================================
#  Summary Index Export - Data Structures
# =====================================================================

class SummaryEventEntry(TypedDict, total=False):
    """Summary entry details for a single event."""
    name: str
    dates: str
    media: list[str]
    location: dict[str, Any]
    circle_count: int
    last_edited: str

class SummaryGroupEntry(TypedDict, total=False):
    """Summary entry details for an event group database."""
    name: str
    events: dict[str, SummaryEventEntry]
    media: list[str]
    links: list[str]

# Defined as TypeAlias of dict[str, Any] to accommodate dynamic subfolder keys
SummaryIndexNode: TypeAlias = dict[str, Any]

# =====================================================================
#  Event List Export - Generation Function
# =====================================================================

def generate_event_list_index(complete_recursive: FolderTree) -> EventListIndexNode:
    """
    Generates the event list index from the complete recursive structure.
    
    Structure format:
    {
      "@databases": {
        "db_folder_name": {
          "aliases": [...],
          "events": {
            "event_name": { "index": int, "dates": str, "circle_count": int | None }
          },
          "last_edited": str (optional)
        }
      },
      "@circles_total_count": int,
      "subfolder_name": { ... (nested folder index) }
    }
    """
    def build_node(node: FolderTree) -> tuple[EventListIndexNode, int]:
        event_groups_here: dict[str, EventListGroupEntry] = {}
        subfolders: dict[str, Any] = {}
        total_count = 0
        
        # Sort keys to ensure deterministic output

        for name in sorted(node.keys()):
            val = node[name]
            from db_structs import EventGroup
            if isinstance(val, EventGroup):
                # Build database event list
                event_objects: dict[str, EventListEventEntry] = {}
                for idx, event in enumerate(val.events):
                    event_objects[event.aliases[0]] = {
                        "index": idx,
                        "dates": event.dates,
                        "circle_count": len(event.circles) if event.circles else None
                    }
                eg_obj: EventListGroupEntry = {
                    "aliases": val.aliases,
                    "events": event_objects
                }
                if val.last_edited:
                    eg_obj["last_edited"] = val.last_edited
                event_groups_here[name] = eg_obj
                total_count += len(val.events)
            elif isinstance(val, dict):
                sub_res, sub_count = build_node(val)
                subfolders[name] = sub_res
                total_count += sub_count
                
        res: EventListIndexNode = {
            "@event_groups_here": event_groups_here,
            "@circles_total_count": total_count
        }
        res.update(subfolders)
        return res, total_count

    index_data, _ = build_node(complete_recursive)
    return index_data

# =====================================================================
#  Summary Index Export - Generation Function
# =====================================================================

def generate_summary_index(complete_recursive: FolderTree) -> SummaryIndexNode:
    """
    Generates the summary index from the complete recursive structure.
    
    Structure format:
    {
      "@event_groups_here": {
        "db_folder_name": {
          "name": str,
          "events": {
            "event_name": {
              "name": str,
              "dates": str,
              "media": list[str] (optional),
              "location": dict (optional),
              "circle_count": int (optional),
              "last_edited": str (optional)
            }
          },
          "media": list[str],
          "links": list[str]
        }
      },
      "@event_groups_total_count": int,
      "subfolder_name": { ... (nested folder index) }
    }
    """
    def build_node(node: FolderTree) -> tuple[SummaryIndexNode, int]:
        event_groups_here: dict[str, SummaryGroupEntry] = {}
        subfolders: dict[str, Any] = {}
        total_count = 0
        
        for name in sorted(node.keys()):
            val = node[name]
            from db_structs import EventGroup
            if isinstance(val, EventGroup):
                # Build database event list
                event_objects: dict[str, SummaryEventEntry] = {}
                for event in val.events:
                    media = [m.path for m in event.media] if event.media else []
                    location = event.locations[0].get_json() if (event.locations and len(event.locations) > 0) else None
                    
                    event_entry: SummaryEventEntry = {
                        "name": event.aliases[0],
                        "dates": event.dates
                    }
                    if media:
                        event_entry["media"] = media
                    if location:
                        event_entry["location"] = location
                    if event.circles:
                        event_entry["circle_count"] = len(event.circles)
                    if event.last_edited:
                        event_entry["last_edited"] = event.last_edited
                        
                    event_objects[event.aliases[0]] = event_entry
                
                # Construct group details
                group_media = [m.path for m in val.media] if val.media else []
                group_entry: SummaryGroupEntry = {
                    "name": val.aliases[0],
                    "events": event_objects,
                    "media": group_media,
                    "links": val.links or []
                }
                event_groups_here[name] = group_entry
                total_count += 1
            elif isinstance(val, dict):
                sub_res, sub_count = build_node(val)
                subfolders[name] = sub_res
                total_count += sub_count
                
        res: SummaryIndexNode = {
            "@event_groups_here": event_groups_here,
            "@event_groups_total_count": total_count
        }
        res.update(subfolders)
        return res, total_count

    index_data, _ = build_node(complete_recursive)
    return index_data

# =====================================================================
#  Circle Index Export - Helpers
# =====================================================================

def make_circle_extensive_eg(eg: EventGroup) -> dict[str, list[dict[str, Any]]]:
    """Formats an EventGroup's events and circles for the extensive circle index."""
    res: dict[str, list[dict[str, Any]]] = {}
    for event in eg.events:
        circle_list_here = []
        if event.circles:
            for circle in event.circles:
                names = []
                names.extend(circle.aliases or [])
                names.extend(circle.pen_names or [])
                
                misc = []
                comment = circle.comments if circle.comments is not None else ""
                misc.append(comment)
                if circle.links:
                    misc.extend(circle.links)
                
                out_dict: dict[str, Any] = {"names": names}
                if misc:
                    out_dict["misc"] = misc
                circle_list_here.append(out_dict)
            res[event.aliases[0]] = circle_list_here
    return res

def make_circle_compact_eg(eg: EventGroup) -> dict[str, list[list[str]]]:
    """Formats an EventGroup's events and circles for the compact circle index."""
    res: dict[str, list[list[str]]] = {}
    for event in eg.events:
        circle_list_here = []
        if event.circles:
            for circle in event.circles:
                names = []
                names.extend(circle.aliases or [])
                names.extend(circle.pen_names or [])
                circle_list_here.append(names)
            res[event.aliases[0]] = circle_list_here
    return res

def split_json_tree(data: dict[str, Any], max_items: int) -> Any:
    """Splits a nested JSON tree into multiple JSON strings, each with up to max_items items in lists.
    Yields JSON strings."""
    file_index = 1
    item_count = 0
    current_root: dict[str, Any] = {}

    def flush():
        """Yield the current root as a JSON string and reset counters"""
        nonlocal file_index, current_root, item_count
        if item_count == 0:
            return
        yield json.dumps(current_root, ensure_ascii=False, indent=None)
        file_index += 1
        current_root = {}
        item_count = 0

    def ensure_path(path_keys):
        """Ensure nested dicts exist in current_root for the given path, rebuild the path if necessary"""
        node = current_root
        for k in path_keys:
            if k not in node:
                node[k] = {}
            node = node[k]
        return node

    def traverse(node, path_keys):
        nonlocal item_count
        nonlocal current_root

        if isinstance(node, list): # Circle list i.e. event
            out_list = []
            parent = ensure_path(path_keys[:-1])
            parent[path_keys[-1]] = out_list

            for item in node:
                if item_count >= max_items:
                    # yield current and reset
                    yield from flush()
                    parent = ensure_path(path_keys[:-1])
                    out_list = []
                    parent[path_keys[-1]] = out_list

                out_list.append(item)
                item_count += 1

                if item_count >= max_items:
                    yield from flush()
                    parent = ensure_path(path_keys[:-1])
                    out_list = []
                    parent[path_keys[-1]] = out_list

        elif isinstance(node, dict): # Subcategory
            for k, v in node.items():
                yield from traverse(v, path_keys + [k])

        else: # leaf value (not list/dict)
            raise ValueError("Invalid structure: leaf value encountered")

    # Traverse the whole tree
    yield from traverse(data, [])
    yield from flush()

# =====================================================================
#  Main Entry Point
# =====================================================================

if __name__ == "__main__":
    print("=== DEA Database Export ===")
    print(f"Reading databases from: {PATH_databases_to_export}")
    
    # Precompute all structures in a single call
    print("Precomputing structures...")
    structures = precompute_structures(PATH_databases_to_export)
    
    print("\n--- Precomputation Summary ---")
    print(f"Total Event Groups (folders): {len(structures.event_group_list)}")
    print(f"Total Events:                 {len(structures.event_list)}")
    print(f"Total Circle Participations:  {len(structures.circle_list)}")
    print("------------------------------")
    
    # ===== Cleaning old exports =====
    if PATH_exported.is_dir():
        print(f"====== Clearing old {PATH_exported.stem} folder... ======")
        # Bypass prompt if run non-interactively or with --yes flag
        if (len(sys.argv) > 1 and sys.argv[1] == "--yes") or not sys.stdin.isatty():
            ans = "YES"
        else:
            ans = input(f"The content of the following folder will be deleted:\n{PATH_exported}\nEnter YES to confirm:\n")
        
        if ans != "YES":
            print("Aborted !")
            sys.exit()
        shutil.rmtree(PATH_exported)
        
    PATH_exported.mkdir(parents=True, exist_ok=True)
    
    # ===== Export event list index =====
    print("====== Generating Event List Index... ======")
    event_list_index = generate_event_list_index(structures.complete_recursive)
    
    path_event_list_index = PATH_exported / "event_list_index.json"
    print(f"Saving event list index to {path_event_list_index}...")
    with path_event_list_index.open("w", encoding=DB_FILE_ENCODING) as f:
        json.dump(event_list_index, f, indent=STATIC_JSON_INDENT, ensure_ascii=False)
        
    # ===== Export summary index =====
    print("====== Generating Summary Index... ======")
    summary_index = generate_summary_index(structures.complete_recursive)
    
    path_summary_index = PATH_exported / "summary_index.json"
    print(f"Saving summary index to {path_summary_index}...")
    with path_summary_index.open("w", encoding=DB_FILE_ENCODING) as f:
        json.dump(summary_index, f, indent=STATIC_JSON_INDENT, ensure_ascii=False)
        
    # ===== Export circle indexes =====
    print("====== Exporting Circle Indexes... ======")
    
    # 1. Extensive Circle Index
    print("Generating extensive circle index...")
    circle_index = serialize_recursive(structures.complete_recursive, make_circle_extensive_eg)
    print("Exporting extensive circle index chunks...")
    extensive_chunk_count = 0
    for i, chunk in enumerate(split_json_tree(circle_index, CIRCLE_INDEX_CIRCLES_PER_FILE)):
        chunk_file_path = PATH_exported / f"circle_participation_extensive_index_{i}.json"
        print(f"Writing chunk {i} to {chunk_file_path}...")
        with chunk_file_path.open("w+", encoding="utf-8") as f:
            f.write(chunk)
        extensive_chunk_count += 1

    # 2. Compact Circle Index
    print("Generating compact circle index...")
    circle_compact_index = serialize_recursive(structures.complete_recursive, make_circle_compact_eg)
    print("Exporting compact circle index chunks...")
    compact_chunk_count = 0
    for i, chunk in enumerate(split_json_tree(circle_compact_index, CIRCLE_COMPACT_INDEX_CIRCLES_PER_FILE)):
        chunk_file_path = PATH_exported / f"circle_participation_compact_index_{i}.json"
        print(f"Writing chunk {i} to {chunk_file_path}...")
        with chunk_file_path.open("w+", encoding="utf-8") as f:
            f.write(chunk)
        compact_chunk_count += 1

    # 3. Circle index metadata
    print("Generating circle index metadata...")
    extensive_total_size = 0
    for i in range(0, extensive_chunk_count):
        chunk_file_path = PATH_exported / f"circle_participation_extensive_index_{i}.json"
        extensive_total_size += chunk_file_path.stat().st_size

    compact_total_size = 0
    for i in range(0, compact_chunk_count):
        chunk_file_path = PATH_exported / f"circle_participation_compact_index_{i}.json"
        compact_total_size += chunk_file_path.stat().st_size
        
    out_metadata = {
        "extensive_index_chunk_count": extensive_chunk_count,
        "compact_index_chunk_count": compact_chunk_count,
        "extensive_total_size": extensive_total_size,
        "compact_total_size": compact_total_size,
    }
    circle_index_metadata_file_path = PATH_exported / "circle_index_metadata.json"
    print(f"Saving circle index metadata file to {circle_index_metadata_file_path}...")
    with circle_index_metadata_file_path.open("w+", encoding="utf-8") as f:
        json.dump(out_metadata, f, ensure_ascii=False, indent=STATIC_JSON_INDENT)

    print("Precomputation and all index exports completed successfully.")

