from modelgym.trainers.trainer import Trainer, eval_metrics
from modelgym.utils.model_space import process_model_spaces
from modelgym.utils import hyperopt2skopt_space
from skopt.optimizer import forest_minimize, gp_minimize

class SkoptTrainer(Trainer):
    def __init__(self, model_spaces, optimizer, tracker=None):
        self.model_spaces = process_model_spaces(model_spaces)
        self.optimizer = optimizer
        self.best_results = {}
        self.ind2names = {}

    def crossval_optimize_params(self, opt_metric, dataset, cv=3, 
                                 opt_evals=50, metrics=None, batch_size=10,
                                 verbose=False):
        for name, model_space in self.model_spaces.items():
            skopt_space, ind2names = hyperopt2skopt_space(model_space.space)
            model_space.space = skopt_space
            self.ind2names[name] = ind2names

        if metrics is None:
            metrics = []

        metrics.append(opt_metric)

        if isinstance(cv, int):
            cv = dataset.cv_split(cv)

        for name, model_space in self.model_spaces.items():

            fn = lambda params: self.crossval_fit_eval(
                model_type=model_space.model_class,
                params=params,
                cv=cv, metrics=metrics, verbose=verbose, space_name=name
            )

            best = self.optimizer(fn, model_space.space,
                                  n_calls=opt_evals,
                                  n_random_starts=min(10, opt_evals))

            if best.fun > self.best_results[name]["loss"]:
                self.best_results[name] = Trainer.crossval_fit_eval(
                    model_space.model_class, best.x, cv, metrics, verbose)

    def get_best_results(self):
        return self.best_results

    def crossval_fit_eval(self, model_type, params, cv, metrics, verbose,
                          space_name):
        params = {self.ind2names[space_name][i]: params[i]
                  for i in range(len(params))}
        result = Trainer.crossval_fit_eval(model_type, params, cv, metrics,
                                           verbose)
        loss = result["loss"]
        best = self.best_results.get(space_name, result)
        if best["loss"] <= result["loss"]:
            self.best_results[space_name] = result
        return best["loss"]

class RFTrainer(SkoptTrainer):
    def __init__(self, model_spaces, tracker=None):
        super().__init__(model_spaces, forest_minimize, tracker)

class GPTrainer(SkoptTrainer):
    def __init__(self, model_spaces, tracker=None):
        super().__init__(model_spaces, gp_minimize, tracker)