"""Explainers.lime module"""
# pylint: disable = import-error, too-few-public-methods, wrong-import-order, line-too-long,
# pylint: disable = unused-argument, duplicate-code, consider-using-f-string, invalid-name
from typing import Dict
import matplotlib.pyplot as plt
import matplotlib as mpl
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.plotting import figure
import pandas as pd

from trustyai import _default_initializer  # pylint: disable=unused-import
from trustyai.utils._visualisation import (
    ExplanationVisualiser,
    DEFAULT_STYLE as ds,
    DEFAULT_RC_PARAMS as drcp,
    bold_red_html,
    bold_green_html,
    output_html,
    feature_html,
)
from trustyai.utils.data_conversions import (
    OneInputUnionType,
    data_conversion_docstring,
    OneOutputUnionType,
)
from trustyai.model import simple_prediction

from org.kie.trustyai.explainability.local.lime import (
    LimeConfig as _LimeConfig,
    LimeExplainer as _LimeExplainer,
)
from org.kie.trustyai.explainability.model import (
    EncodingParams,
    PredictionProvider,
    Saliency,
    SaliencyResults,
    PerturbationContext,
)

from java.util import Random

LimeConfig = _LimeConfig


class LimeResults(ExplanationVisualiser):
    """Wraps LIME results. This object is returned by the :class:`~LimeExplainer`,
    and provides a variety of methods to visualize and interact with the explanation.
    """

    def __init__(self, saliencyResults: SaliencyResults):
        """Constructor method. This is called internally, and shouldn't ever need to be used
        manually."""
        self._saliency_results = saliencyResults

    def map(self) -> Dict[str, Saliency]:
        """
        Return a dictionary of found saliencies.

        Returns
        -------
        Dict[str, Saliency]
             A dictionary of :class:`~trustyai.model.Saliency` objects, keyed by output name.
        """
        return {
            entry.getKey(): entry.getValue()
            for entry in self._saliency_results.saliencies.entrySet()
        }

    def as_dataframe(self) -> pd.DataFrame:
        """
        Return the LIME result as a dataframe.

        Returns
        -------
        pandas.DataFrame
            DataFrame containing the results of the LIME explanation. For each model output, the
            table will contain the following columns:

            * ``${output_name}_features``: The names of each input feature.
            * ``${output_name}_score``: The LIME saliency of this feature.
            * ``${output_name}_value``: The original value of each feature.
            * ``${output_name}_confidence``: The confidence of the reported saliency.
        """
        outputs = self.map().keys()

        data = {}
        for output in outputs:
            pfis = self.map().get(output).getPerFeatureImportance()
            data[f"{output}_features"] = [
                f"{pfi.getFeature().getName()}" for pfi in pfis
            ]
            data[f"{output}_score"] = [pfi.getScore() for pfi in pfis]
            data[f"{output}_value"] = [
                pfi.getFeature().getValue().as_number() for pfi in pfis
            ]
            data[f"{output}_confidence"] = [pfi.getConfidence() for pfi in pfis]

        return pd.DataFrame.from_dict(data)

    def as_html(self) -> pd.io.formats.style.Styler:
        """
        Return the LIME result as a Pandas Styler object.

        Returns
        -------
        pandas.Styler
            Styler containing the results of the LIME explanation, in the same
            schema as in :func:`as_dataframe`. Currently, no default styles are applied
            in this particular function, making it equivalent to :code:`self.as_dataframe().style`.
        """
        return self.as_dataframe().style

    def plot(self, decision: str) -> None:
        """Plot the LIME saliencies."""
        with mpl.rc_context(drcp):
            dictionary = {}
            for feature_importance in (
                self.map().get(decision).getPerFeatureImportance()
            ):
                dictionary[
                    feature_importance.getFeature().name
                ] = feature_importance.getScore()

            colours = [
                ds["negative_primary_colour"]
                if i < 0
                else ds["positive_primary_colour"]
                for i in dictionary.values()
            ]
            plt.title(f"LIME explanation of {decision}")
            plt.barh(
                range(len(dictionary)),
                dictionary.values(),
                align="center",
                color=colours,
            )
            plt.yticks(range(len(dictionary)), list(dictionary.keys()))
            plt.tight_layout()
            plt.show()

    def _get_bokeh_plot_dict(self):
        plot_dict = {}
        for output_name, value in self.map().items():
            lime_data_source = pd.DataFrame(
                [
                    {
                        "feature": str(pfi.getFeature().getName()),
                        "saliency": pfi.getScore(),
                    }
                    for pfi in value.getPerFeatureImportance()
                ]
            )
            lime_data_source["color"] = lime_data_source["saliency"].apply(
                lambda x: ds["positive_primary_colour"]
                if x >= 0
                else ds["negative_primary_colour"]
            )
            lime_data_source["saliency_colored"] = lime_data_source["saliency"].apply(
                lambda x: (bold_green_html if x >= 0 else bold_red_html)(
                    "{:.2f}".format(x)
                )
            )

            lime_data_source["color_faded"] = lime_data_source["saliency"].apply(
                lambda x: ds["positive_primary_colour_faded"]
                if x >= 0
                else ds["negative_primary_colour_faded"]
            )
            source = ColumnDataSource(lime_data_source)
            htool = HoverTool(
                names=["bars"],
                tooltips="<h3>LIME</h3> {} saliency to {}: @saliency_colored".format(
                    feature_html("@feature"), output_html(output_name)
                ),
            )
            bokeh_plot = figure(
                sizing_mode="stretch_both",
                title="Lime Feature Importances",
                y_range=lime_data_source["feature"],
                tools=[htool],
            )
            bokeh_plot.hbar(
                y="feature",
                left=0,
                right="saliency",
                fill_color="color_faded",
                line_color="color",
                hover_color="color",
                color="color",
                height=0.75,
                name="bars",
                source=source,
            )
            bokeh_plot.line([0, 0], [0, len(lime_data_source)], color="#000")
            bokeh_plot.xaxis.axis_label = "Saliency Value"
            bokeh_plot.yaxis.axis_label = "Feature"
            plot_dict[output_name] = bokeh_plot
        return plot_dict


class LimeExplainer:
    """*"Which features were most important to the results?"*

    LIME (`Local Interpretable Model-agnostic Explanations <https://arxiv.org/abs/1602.04938>`_)
    seeks to answer this question via providing *saliencies*, weights associated with each input
    feature that describe how strongly said feature contributed to the model's output.
    """

    def __init__(self, samples=10, **kwargs):
        """Initialize the :class:`LimeExplainer`.

        Parameters
        ----------
        samples: int
            Number of samples to be generated for the local linear model training.

        Keyword Arguments:
            * penalise_sparse_balance : bool
                (default=True) Whether to penalise features that are likely to produce linearly
                inseparable outputs. This can improve the efficacy and interpretability of the
                outputted saliencies.
            * normalise_weights : bool
                (default=False) Whether to normalise the saliencies generated by LIME. If selected,
                saliencies will be normalized between 0 and 1.
            * use_wlr_model : bool
                (default=True) Whether to use a weighted linear regression as the LIME explanatory
                model. If `false`, a multilayer perceptron is used, which generally has a slower
                runtime,
            * seed: int
                (default=0) The random seed to be used.
            * perturbations: int
                (default=1) The starting number of feature perturbations within the explanation
                process.
            * trackCounterfactuals : bool
                (default=False) Keep track of produced byproduct counterfactuals during LIME run.

        """
        self._jrandom = Random()
        self._jrandom.setSeed(kwargs.get("seed", 0))

        self._lime_config = (
            LimeConfig()
            .withNormalizeWeights(kwargs.get("normalise_weights", False))
            .withPerturbationContext(
                PerturbationContext(self._jrandom, kwargs.get("perturbations", 1))
            )
            .withSamples(samples)
            .withEncodingParams(EncodingParams(0.07, 0.3))
            .withAdaptiveVariance(True)
            .withPenalizeBalanceSparse(kwargs.get("penalise_sparse_balance", True))
            .withUseWLRLinearModel(kwargs.get("use_wlr_model", True))
            .withTrackCounterfactuals(kwargs.get("track_counterfactuals", False))
        )

        self._explainer = _LimeExplainer(self._lime_config)

    @data_conversion_docstring("one_input", "one_output")
    def explain(
        self,
        inputs: OneInputUnionType,
        outputs: OneOutputUnionType,
        model: PredictionProvider,
    ) -> LimeResults:
        """Produce a LIME explanation.

        Parameters
        ----------
        inputs : {}
            The input features to the model, as a: {}
        outputs : {}
            The corresponding model outputs for the provided features, that is,
            ``outputs = model(input_features)``. These can take the form of a: {}
        model : :obj:`~trustyai.model.PredictionProvider`
            The TrustyAI PredictionProvider, as generated by :class:`~trustyai.model.Model`
            or :class:`~trustyai.model.ArrowModel`.

        Returns
        -------
        :class:`~LimeResults`
            Object containing the results of the LIME explanation.
        """
        _prediction = simple_prediction(inputs, outputs)
        return LimeResults(self._explainer.explainAsync(_prediction, model).get())