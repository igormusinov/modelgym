from modelgym.trainers.trainer import Trainer
from modelgym.utils.model_space import process_model_spaces
from modelgym.utils import hyperopt2skopt_space
from modelgym.utils.evaluation import crossval_fit_eval
from skopt.optimizer import forest_minimize, gp_minimize, Optimizer
from sklearn.externals.joblib import Parallel, delayed
from modelgym.utils import cat_preprocess_cv

import numpy as np

import time
import pickle
import asyncio


class SkoptTrainer(Trainer):
    """SkoptTrainer is a class for models hyperparameter optimization, based on skopt library"""

    def __init__(self, model_spaces, optimizer, tracker=None):
        """
        Args:
            model_spaces (list of modelgym.models.Model or modelgym.utils.ModelSpaces): list of model spaces
                (model classes and parameter spaces to look in). If some list item is Model, it is
                converted in ModelSpace with default space and name equal to model class __name__
            optimizer (function, e.g forest_minimize or gp_minimize
            tracker (modelgym.trackers.Tracker, optional): ignored
        Raises:
            ValueError if there are several model_spaces with similar names
        """
        super().__init__(model_spaces, tracker)
        self.model_spaces = process_model_spaces(model_spaces)
        self.optimizer = optimizer
        self.best_results = {}
        self.ind2names = {}

    def crossval_optimize_params(self, opt_metric, dataset, cv=3,
                                 opt_evals=50, metrics=None,
                                 verbose=False, client=None, workers=1, **kwargs):
        """Find optimal hyperparameters for all models

        Args:
            opt_metric (modelgym.metrics.Metric): metric to optimize
            dataset (modelgym.utils.XYCDataset or None): dataset
            cv (int or list of tuples of (XYCDataset, XYCDataset)): if int, then number of cross-validation folds or
                cross-validation folds themselves otherwise.
            opt_evals (int): number of cross-validation evaluations
            metrics (list of modelgym.metrics.Metric, optional): additional metrics to evaluate
            verbose (bool): Enable verbose output.
            **kwargs: ignored
        Note:
            if cv is int, than dataset is split into cv parts for cross validation. Otherwise, cv folds are used.
        """

        for name, model_space in self.model_spaces.items():
            ##skopt_space, ind2names = hyperopt2skopt_space(model_space.space)
            self.ind2names[name] = [param.name for param in model_space.space]

        if metrics is None:
            metrics = []

        metrics.append(opt_metric)

        if isinstance(cv, int) and client is None:
            cv = dataset.cv_split(cv)
        #  Different signatures of functions
        # if client is None:
        #     fn = self._eval_fn
        # else:
        #     fn = client.eval

        for name, model_space in self.model_spaces.items():

            '''
            best = self.optimizer(fn, model_space.space,
                                  n_calls=opt_evals,
                                  n_random_starts=min(10, opt_evals))
            '''

            # Check Optimizer options
            optimizer = Optimizer(
                dimensions=model_space.space,
                random_state=1,
                acq_func="gp_hedge"
            )
            ioloop = asyncio.get_event_loop()
            for i in range(opt_evals // workers):
                #
                x = optimizer.ask(n_points=workers)  # x is a list of n_points points
                x_named = []
                for params in x:
                    x_named.append({self.ind2names[name][i]: params[i]
                                    for i in range(len(params))})

                tasks = [ioloop.create_task(client.eval_model(
                    model_type=model_space.model_class,
                    params=params,
                    data_path=dataset,
                    cv=cv, metrics=metrics)) for params in
                    x_named]
                y = ioloop.run_until_complete(asyncio.gather(*tasks))

                """
                if client is None:
                    y = Parallel()(delayed(self._eval_fn)(
                        model_type=model_space.model_class,
                        params=params,
                        cv=cv, metrics=metrics, verbose=verbose, space_name=name) for params in
                                   x_named)  # evaluate points in parallel
                else:
                    pickle.dumps(client.eval)

                    y = Parallel()(delayed(client.eval)(
                        model_type=model_space.model_class,
                        params=params,
                        data_path=dataset,
                        cv=cv, metrics=metrics, verbose=verbose) for params in x_named)  # evaluate points in parallel
                """
                best = optimizer.tell(x, y)
                #
            ioloop.close()
            if not (name in self.best_results) or best.fun > self.best_results.get(name).get("loss"):
                self.best_results[name] = client.eval_model(
                    model_space.model_class, {self.ind2names[name][i]: params[i]
                                              for i in range(len(best.x))}, dataset, cv, metrics)
        return best

    def get_best_results(self):
        """When training is complete, return best parameters (and additional information) for each model space

        Returns:
            dict of shape::

                {
                    name (str): {
                        "result": {
                            "loss": float,
                            "metric_cv_results": list,
                            "params": dict
                        },
                        "model_space": modelgym.utils.ModelSpace
                    }
                }

            name is a name of corresponding model_space,

            metric_cv_results contains dict's from metric names to calculated metric values for each fold in cv_fold,

            params is optimal parameters of corresponding model,

            model_space is corresponding model_space.
        """
        return {name: {"result": result,
                       "model_space": self.model_spaces[name]}
                for (name, result) in self.best_results.items()}

    def _eval_fn(self, model_type, params, cv, metrics, verbose, space_name):
        """Evaluates function to minimize and stores additional info (metrics, params) if it is current best result
        Args:
            model_type (type, subclass of Model)
            params (dict of str:obj): model parameters
            cv (list of tuple like (XYCDataset, XYCDataset)): cross validation folds
            metrics (list of modelgym.metrics.Metric): metrics to evaluate.
                Last metric is considered to be either loss (if metric.is_min_optimal is True) or -loss.
                Loss is the metric we want to minimize.
            verbose (bool): Enable verbose output.
            space_name (str): name of optimized model_space
        Returns:
            float: loss
        """
        print("Start learning")
        time1 = time.time()
        # Do it before _eval_fn
        # params = {self.ind2names[space_name][i]: params[i]
        #           for i in range(len(params))}
        result = crossval_fit_eval(model_type, params, cv, metrics, verbose)
        best = self.best_results.get(space_name, result)
        # if best["loss"] <= result["loss"]:
        #     self.best_results[space_name] = result
        print("End after %s", time.time() - time1)
        return best["loss"]


class RFTrainer(SkoptTrainer):
    """RFTrainer is a SkoptTrainer, using Sequential optimisation using decision trees"""

    def __init__(self, model_spaces, tracker=None):
        super().__init__(model_spaces, forest_minimize, tracker)


class GPTrainer(SkoptTrainer):
    """GPTrainer is a SkoptTrainer, using Bayesian optimization using Gaussian Processes."""

    def __init__(self, model_spaces, tracker=None):
        super().__init__(model_spaces, gp_minimize, tracker)
