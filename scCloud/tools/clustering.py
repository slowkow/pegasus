import time
import numpy as np
import pandas as pd
from joblib import Parallel, delayed, effective_n_jobs
from natsort import natsorted

import ctypes
import ctypes.util

import louvain
import leidenalg
from sklearn.cluster import KMeans
from typing import List

from scCloud.tools import construct_graph



def louvain(
    data: 'AnnData',
    rep: str = 'pca',
    resolution: int = 1.3,
    random_state: int = 0,
    class_label: str = 'louvain_labels',
) -> None:
    """
    TODO: Documentation
    """

    start = time.time()

    rep_key = 'W_' + rep
    if rep_key not in data.uns:
        raise ValueError("Cannot find affinity matrix. Please run neighbors first!")
    W = data.uns[rep_key]

    G = construct_graph(W)
    partition_type = louvain.RBConfigurationVertexPartition
    partition = partition_type(G, resolution_parameter=resolution, weights='weight')
    optimiser = louvain.Optimiser()
    optimiser.set_rng_seed(random_state)
    diff = optimiser.optimise_partition(partition)

    labels = np.array([str(x + 1) for x in partition.membership])
    categories = natsorted(np.unique(labels))
    data.obs[class_label] = pd.Categorical(values=labels, categories=categories)
    
    end = time.time()
    print("Louvain clustering is done. Time spent = {:.2f}s.".format(end - start))


def leiden(
    data: 'AnnData',
    rep: str = 'pca',
    resolution: int = 1.3,
    n_iter: int = -1,
    random_state: int = 0,
    class_label: str = 'leiden_labels',
) -> None:
    """
    TODO: Documentation.
    """

    start = time.time()

    rep_key = 'W_' + rep
    if rep_key not in data.uns:
        raise ValueError("Cannot find affinity matrix. Please run neighbors first!")
    W = data.uns[rep_key]

    G = construct_graph(W)
    partition_type = leidenalg.RBConfigurationVertexPartition
    partition = leidenalg.find_partition(
        G,
        partition_type,
        seed=random_state,
        weights='weight',
        resolution_parameter=resolution,
        n_iterations=n_iter,
    )

    labels = np.array([str(x + 1) for x in partition.membership])
    categories = natsorted(np.unique(labels))
    data.obs[class_label] = pd.Categorical(values=labels, categories=categories)
    
    end = time.time()
    print("Leiden clustering is done. Time spent = {:.2f}s.".format(end - start))


def set_numpy_thread_to_one():
    library_type = None
    library_obj = None
    previous_num = None

    mkl_loc = ctypes.util.find_library('mkl_rt')
    if mkl_loc is not None:
        mkl_lib = ctypes.cdll.LoadLibrary(mkl_loc)

        library_type = 'mkl'
        library_obj = mkl_lib
        previous_num = mkl_lib.mkl_get_max_threads()

        mkl_lib.mkl_set_num_threads(ctypes.byref(ctypes.c_int(1)))
    else:
        openblas_loc = ctypes.util.find_library('openblas')
        if openblas_loc is not None:
            openblas_lib = ctypes.cdll.LoadLibrary(openblas_loc)

            library_type = 'openblas'
            library_obj = openblas_lib
            previous_num = openblas_lib.openblas_get_num_threads()

            openblas_lib.openblas_set_num_threads(1)
        else:
            import os
            import glob

            files = glob.glob(
                os.path.join(os.path.dirname(np.__file__), '.libs', 'libopenblas*.so')
            )
            if len(files) == 1:
                path, openblas_loc = os.path.split(files[0])
                part2 = (
                    ':' + os.environ['LD_LIBRARY_PATH']
                    if 'LD_LIBRARY_PATH' in os.environ
                    else ''
                )
                os.environ['LD_LIBRARY_PATH'] = path + part2
                openblas_lib = ctypes.cdll.LoadLibrary(openblas_loc)

                library_type = 'openblas'
                library_obj = openblas_lib
                previous_num = openblas_lib.openblas_get_num_threads()

                openblas_lib.openblas_set_num_threads(1)

    return library_type, library_obj, previous_num


def recover_numpy_thread(library_type: str, library_obj: object, value: int):
    if library_type == 'mkl':
        library_obj.mkl_set_num_threads(ctypes.byref(ctypes.c_int(value)))
    elif library_type == 'openblas':
        library_obj.openblas_set_num_threads(value)


def run_one_instance_of_kmeans(n_clusters: int, X: 'np.array', seed: int) -> List[str]:
    library_type, library_obj, value = set_numpy_thread_to_one()
    km = KMeans(n_clusters=n_clusters, n_init=1, n_jobs=1, random_state=seed)
    km.fit(X)
    recover_numpy_thread(library_type, library_obj, value)
    return km.labels_


def run_multiple_kmeans(
    data: 'AnnData', rep: 'str', n_jobs: int, n_clusters: int, n_init: int, random_state: int, temp_folder: None
) -> List[str]:
    """ Spectral clustering in parallel
    """
    start = time.time()

    n_jobs = effective_n_jobs(n_jobs)

    rep_key = 'X_' + rep
    X = data.obsm[rep_key].astype('float64')

    np.random.seed(random_state)
    seeds = np.random.randint(np.iinfo(np.int32).max, size=n_init)
    results = Parallel(n_jobs=n_jobs, max_nbytes=1e7, temp_folder=temp_folder)(
        delayed(run_one_instance_of_kmeans)(n_clusters, X, seed) for seed in seeds
    )  # Note that if n_jobs == 1, joblib will not fork a new process.

    labels = list(zip(*results))
    uniqs = np.unique(labels, axis=0)
    transfer_dict = {tuple(k): v for k, v in zip(uniqs, range(uniqs.shape[0]))}
    labels = [transfer_dict[x] for x in labels]

    end = time.time()
    print("run_multiple_kmeans finished in {:.2f}s.".format(end - start))

    return labels


def spectral_louvain(
    data: 'AnnData',
    rep: str = 'pca',
    resolution: float = 1.3,
    rep_kmeans: str = 'diffmap',
    n_clusters: str = 30,
    n_init: int = 20,
    n_jobs: int = -1,
    random_state: int = 0,
    temp_folder: str = None,
    class_label: str = 'spectral_louvain_labels',
) -> None:
    """
    TODO: Documentation.
    rep_kmeans: representation to run kmeans, default is diffmap.
    """

    start = time.time()

    if 'X_' + rep_kmeans not in data.obsm.keys():
        raise ValueError("Please run {} first!".foramt(rep_kmeans))
    if 'W_' + rep not in data.uns:
        raise ValueError("Cannot find affinity matrix. Please run neighbors first!")


    labels = run_multiple_kmeans(
        data, rep_kmeans, n_jobs, n_clusters, n_init, random_state, temp_folder
    )

    W = data.uns['W_' + rep]

    G = construct_graph(W)
    partition_type = louvain.RBConfigurationVertexPartition
    partition = partition_type(
        G, resolution_parameter=resolution, weights='weight', initial_membership=labels
    )
    partition_agg = partition.aggregate_partition()

    optimiser = louvain.Optimiser()
    optimiser.set_rng_seed(random_state)
    diff = optimiser.optimise_partition(partition_agg)
    partition.from_coarse_partition(partition_agg)

    labels = np.array([str(x + 1) for x in partition.membership])
    categories = natsorted(np.unique(labels))
    data.obs[class_label] = pd.Categorical(values=labels, categories=categories)

    end = time.time()
    print(
        "Spectral Louvain clustering is done. Time spent = {:.2f}s.".format(
            end - start
        )
    )


def spectral_leiden(
    data: 'AnnData',
    rep: str = 'pca',
    resolution: float = 1.3,
    rep_kmeans: str = 'diffmap',
    n_clusters: str = 30,
    n_init: int = 20,
    n_jobs: int = -1,
    random_state: int = 0,
    temp_folder: str = None,
    class_label: str = 'spectral_leiden_labels',
) -> None:
    """
    TODO: Documentation.
    """

    start = time.time()

    if 'X_' + rep_kmeans not in data.obsm.keys():
        raise ValueError("Please run {} first!".foramt(rep_kmeans))
    if 'W_' + rep not in data.uns:
        raise ValueError("Cannot find affinity matrix. Please run neighbors first!")


    labels = run_multiple_kmeans(
        data, rep_kmeans, n_jobs, n_clusters, n_init, random_state, temp_folder
    )

    W = data.uns['W_' + rep]

    G = construct_graph(W)
    partition_type = leidenalg.RBConfigurationVertexPartition
    partition = partition_type(
        G, resolution_parameter=resolution, weights='weight', initial_membership=labels
    )
    partition_agg = partition.aggregate_partition()

    optimiser = leidenalg.Optimiser()
    optimiser.set_rng_seed(random_state)
    diff = optimiser.optimise_partition(partition_agg, -1)
    partition.from_coarse_partition(partition_agg)

    labels = np.array([str(x + 1) for x in partition.membership])
    categories = natsorted(np.unique(labels))
    data.obs[class_label] = pd.Categorical(values=labels, categories=categories)
    
    end = time.time()
    print(
        "Approximated Leiden clustering is done. Time spent = {:.2f}s.".format(
            end - start
        )
    )
