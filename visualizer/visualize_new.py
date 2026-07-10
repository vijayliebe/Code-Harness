import argparse
import json
import os
import sys

import numpy as np


def _find_persist_dir(config_path: str = "") -> str:
    candidates = [
        os.path.join(os.getcwd(), ".code-harness/chromadb"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".code-harness/chromadb"),
    ]
    if config_path:
        try:
            with open(config_path) as f:
                import json as _json
                cfg = _json.load(f) if config_path.endswith(".json") else __import__("yaml").safe_load(f)
            pd = cfg.get("vector_store", {}).get("persist_directory", "")
            if pd:
                candidates.insert(0, os.path.abspath(pd))
        except Exception:
            pass
    for cand in candidates:
        if os.path.exists(cand):
            return cand
    return ""


def main():
    parser = argparse.ArgumentParser(description="Code Harness Vector Space Visualizer")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path for the HTML visualization")
    args = parser.parse_args()

    config_path = os.environ.get("CODE_HARNESS_CONFIG", "")
    persist_dir = _find_persist_dir(config_path)

    if not persist_dir:
        print("Error: Could not locate the '.code-harness/chromadb' directory.")
        print("Please ensure you have indexed the repository first using: python main.py index <repo_path>")
        return

    import chromadb
    from chromadb.config import Settings as ChromaSettings

    collection_name = "code_chunks"
    print(f"Connecting to ChromaDB at '{persist_dir}'...")
    client = chromadb.PersistentClient(path=persist_dir, settings=ChromaSettings(anonymized_telemetry=False))
    try:
        collection = client.get_collection(collection_name)
    except Exception:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass
        try:
            collection = client.create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception:
            print("Error: Could not access or create ChromaDB collection.")
            return

    count = collection.count()
    print(f"Found {count} chunks in ChromaDB.")
    if count == 0:
        print("No chunks found in the database. Please index the repository first.")
        return

    # Get all embeddings, documents, and metadata
    data = collection.get(include=['embeddings', 'metadatas', 'documents'])
    
    embeddings = data.get('embeddings')
    metadatas = data.get('metadatas')
    documents = data.get('documents')
    ids = data.get('ids')
    
    if embeddings is None or len(embeddings) == 0:
        print("Error: No embeddings found. Make sure ChromaDB was populated with embedding data.")
        return
        
    # Convert embeddings to numpy array
    emb_matrix = np.array(embeddings)
    print(f"Loaded embeddings matrix of shape: {emb_matrix.shape}")
    
    # Compute 2D projection via SVD PCA (only numpy dependency)
    print("Computing 2D PCA projection...")
    try:
        mean = np.mean(emb_matrix, axis=0)
        centered = emb_matrix - mean
        U, S, Vt = np.linalg.svd(centered, full_matrices=False)
        projected = centered @ Vt[:2].T
        x_coords = projected[:, 0].tolist()
        y_coords = projected[:, 1].tolist()
        print("PCA projection completed successfully.")
    except Exception as e:
        print(f"Warning: PCA projection failed ({e}). Using simple fallback projection.")
        x_coords = emb_matrix[:, 0].tolist() if emb_matrix.shape[1] > 0 else [0.0] * len(embeddings)
        y_coords = emb_matrix[:, 1].tolist() if emb_matrix.shape[1] > 1 else [0.0] * len(embeddings)
        
    # Structure points for frontend
    points = []
    unique_files = set()
    unique_types = set()
    unique_repos = set()
    
    for i in range(len(ids)):
        meta = metadatas[i] if metadatas else {}
        doc = documents[i] if documents else ""
        
        file_path = meta.get("file_path", "unknown")
        entity_name = meta.get("entity_name", "unknown")
        entity_type = meta.get("entity_type", "unknown")
        repo_name = meta.get("repo_name", "unknown")
        
        unique_files.add(file_path)
        unique_types.add(entity_type)
        unique_repos.add(repo_name)
        
        points.append({
            "id": ids[i],
            "chunk_id": meta.get("chunk_id", ids[i]),
            "file_path": file_path,
            "entity_name": entity_name,
            "entity_type": entity_type,
            "repo_name": repo_name,
            "start_line": int(meta.get("start_line", 0)),
            "end_line": int(meta.get("end_line", 0)),
            "x": x_coords[i],
            "y": y_coords[i],
            "content": doc
        })
        
    # Prepare output folder
    output_path = args.output
    if not output_path:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "chroma_visualization.html")
    # Load inter-repo graph if available
    repo_graph_data = {"nodes": [], "edges": []}
    code_harness_dir = os.path.dirname(os.path.normpath(persist_dir))
    for gf in (os.path.join(code_harness_dir, "repo_graph.json"),
               os.path.join(os.getcwd(), ".code-harness", "repo_graph.json")):
        if os.path.exists(gf):
            repo_graph_path = gf
            break
    else:
        repo_graph_path = ""
    if repo_graph_path:
        try:
            with open(repo_graph_path) as f:
                raw_graph = json.load(f)
            for node in raw_graph.get("nodes", []):
                repo_graph_data["nodes"].append({
                    "name": node.get("id", ""),
                    "entity_count": node.get("entity_count", 0),
                    "imports": node.get("imports", []),
                    "exports": node.get("exports", []),
                })
            for link in raw_graph.get("links", []):
                repo_graph_data["edges"].append({
                    "source": link.get("source", ""),
                    "target": link.get("target", ""),
                    "relationship": link.get("relationship", "related"),
                    "modules": link.get("modules", []),
                    "shared_entities": link.get("shared_entities", []),
                    "depends_on_modules": link.get("depends_on_modules", []),
                })
            print(f"Loaded inter-repo graph: {len(repo_graph_data['nodes'])} repos, {len(repo_graph_data['edges'])} relationships")
        except Exception as e:
            print(f"Warning: Could not load repo graph: {e}")

    # Write the visualizer HTML file
    html_content = get_html_template(
        points,
        sorted(list(unique_files)),
        sorted(list(unique_types)),
        sorted(list(unique_repos)),
        repo_graph_data,
    )
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print(f"\nSuccess! Open standard web browser to view the visualization:")
    print(f"file://{os.path.abspath(output_path)}")

