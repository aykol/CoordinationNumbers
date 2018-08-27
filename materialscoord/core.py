import yaml
import abc
import os
import glob
import pandas as pd
from collections import Counter
from collections import OrderedDict
from pymatgen.core.structure import Structure, Molecule
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from pymatgen.analysis.local_env import NearNeighbors
import re

module_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))

class Benchmark(object):
    """
    Class for performing CN benchmarks on a set of structures using the selected set of methods.

    :param structure_groups (str or list): name of test structure directory. Defaults to "elemental".
    :param custom_set (str): full path to custom set of external structures to be loaded. Can be used
           to apply CN methods on user-specified structures.
    :param unique_sites (bool): Only calculates CNs of symmetrically unique sites in structures. This
           is essential to get a cleaner output. ICSD cif files already use unique sites so either
           True/False will show the same sites. Most useful for MP cif files. Defaults to True.
    :param nround (int): rounds CNs to given number of decimals. Defaults to 3. nround=0 means no rounding.
    :param use_weights (bool): Whether or not to use CN method weighting scheme. Defaults to False.
    :param cation_anion (bool): Considers only cations as central atoms for which CN is to be computed and
           atoms without oxidation states (i.e., metals). Defaults to False.
    :param anion_cation (bool): Considers only anions as central atoms. Defaults to False.

    TODO: make constructor more flexible by using a list of structures.
    TODO: Move the _load... function to a from_preset() class method so that a user
    TODO: conveniently can use/access our predefined structure test sets, but s/he has also the
    TODO: opportunity to set up any structure test set by her/his own.

    TODO: if cation_anion and anion_cation are both True or both False... what happens? (haven't tested)
    TODO: right now, to calculate cation_anion + anion_cation interactions, use jupyter nb to sum
    TODO: test out custom_set, see Murat's documentation
    """

    def __init__(self, cations, anions, test_structures,
                 unique_sites=True, nround=3, use_weights=False,
                 cation_anion=True, anion_cation=False):
        self.test_structures = test_structures
        self.cations = cations
        self.anions = anions
        self.unique_sites = unique_sites
        self.nsites = None
        self.nround = nround
        self.use_weights = use_weights
        self.cation_anion = cation_anion
        self.anion_cation = anion_cation

    @classmethod
    def from_preset(cls, preset_name):
        """
        Loads cations, anions, and test_structure dictionaries from structure_groups.
        Cation and anion dictionaries only work when oxidation states are present in cif files (e.g., ICSD).

        - cations dictionary includes cation elements and elements with '0' oxidation states/metals (i.e., Ca, Al)
        - anions dictionary includes anion elements (e.g., O, F)
        - test_structure dictionary includes structure from cif file

        Oxidation states are removed from elements.

        :param group (str): test structure directory name(s). Defaults to "elemental".

        TODO: Use predict_oxidation_states() for MP cif file without oxidation state labels?
        TODO: Otherwise, NPFuncs doesn't work with MP cif files.
        """
        str_files = []
        preset_name = preset_name if isinstance(preset_name, list) else [preset_name]
        for pn in preset_name:
            p = os.path.join(module_dir, "..", "test_structures", pn, "*")
            sf = glob.glob(p)
            str_files.extend(sf)

        cations = OrderedDict()
        anions = OrderedDict()
        test_structures = OrderedDict()
        for s in str_files:
            name = os.path.basename(s).split(".")[0]
            structure = Structure.from_file(s)

            # if self.perturb:
            #     structure = structure.copy()
            #     structure.perturb(self.perturb)

            if preset_name == "clusters":
                oxi = {"Al": 3, "H": 1, "O": -2}
                structure.add_oxidation_state_by_element(oxidation_states=oxi)

            cats = []
            ans = []
            for i in structure:
                i = str(i).split(']', 1)[1]
                if i.endswith('+'):
                    if '0' not in i:  # metals
                        el = ''.join(x for x in str(i) if x.isalpha())
                        if el not in cats:
                            cats.append(el)
                if str(i).endswith('-'):
                    el = ''.join(x for x in str(i) if x.isalpha())
                    if el not in ans:
                        ans.append(el)
            cations[name] = cats
            anions[name] = ans

            structure.remove_oxidation_states()
            test_structures[name] = structure

        return cls(cations, anions, test_structures)

    def benchmark(self, methods):
        """
        Calculates CN for each structure site using NN method(s).

        nsites (int) is used to determine the number of sites each structure has
        and uses the max number of sites as the number of columns in the framework.

        :param methods (list): CN methods from pymatgen.analysis.local_env

        :returns cn benchmarks as pandas dataframe. Used in NBFuncs to calculate benchmark score.

        TODO: Perhaps it would be better to report the tuple (site, {el: coord}). Or does it matter? Will think about this.
        """

        cns = {}
        for m in methods:
            assert isinstance(m, (NearNeighbors, HIBase))
            cns[m] = {}

        nsites = []
        for m in methods:
            for name, structure in self.test_structures.items():
                cns[m][name] = []
                if self.unique_sites:
                    es = SpacegroupAnalyzer(structure).get_symmetrized_structure().equivalent_sites
                    sites = [structure.index(x[0]) for x in es]
                else:
                    sites = range(len(structure))

                for j in sites:
                    if isinstance(m, NearNeighbors):
                        tmpcn = m.get_cn_dict(structure, j, self.use_weights)
                        if m.__class__.__name__ == "MinimumVIRENN":
                            kdict = {}
                            for k,v in tmpcn.items():
                                k = re.sub('[^a-zA-Z]+', '', k)
                                kdict[k] = v
                            tmpcn = kdict
                    else:
                        tmpcn = m.compute(structure, j)
                        if tmpcn == "null":
                            continue
                    if self.nround:
                        tmpcn = self._roundcns(tmpcn, self.nround)
                    cns[m][name].append((structure[j].species_string, tmpcn))
                if self.cation_anion:
                    for mat, cat in self.cations.items():
                        if (name == mat) and cat:
                            cns[m][name] = self._popel(cns[m][name], cat)
                elif self.anion_cation:
                    for mat, an in self.anions.items():
                        if name == mat:
                            cns[m][name] = self._popel(cns[m][name], an)
                nsites.append(len(cns[m][name]))
        self.nsites = max(nsites)

        data = {}
        for m in methods:
            sc_dict = {}
            for site in range(self.nsites):
                data[m.__class__.__name__ + str(site)] = {}
            for struc in cns[m]:
                temp = []
                for mat, ions in cns[m][struc]:
                    if isinstance(ions, dict):
                        temp.append((mat, ions))
                sc_dict[struc] = temp
                for struc, ions in sc_dict.items():
                    l = len(ions)
                    if l < self.nsites:
                        for emp_site in range(self.nsites - l):
                            ions.append(("null", {}))
                    for site in range(self.nsites):
                        #tdata[m.__class__.__name__ + str(site)][struc] = (ions[site][0], ions[site][1])
                        data[m.__class__.__name__ + str(site)][struc] = ions[site][1]
        return pd.DataFrame(data=data)

    @staticmethod
    def _roundcns(d, ndigits):
        """
        rounds all values in a dict to ndigits. Use when use_weights = True.

        :param d (dict): dictionary of values to be rounded
        :param ndigits (int): number of digits to round to
        :returns dict with rounded values
        """
        dout = dict(d)
        for k,v in dout.items():
            if not isinstance(v, list):
                dout[k]=round(v, ndigits)
        return dout

    @staticmethod
    def _popel(cns, ions):
        """
        Only use coordination of ion (cation to anion / anion to cation). All other interactions (i.e., cation-cation,
        anion-anion) are deleted ('pop'ed). This is because we are interested in only interactions between
        atoms with opposite charges (unless all atoms have same charge).

        :param cns (list): list of tuples (site, {el: coord}).
               Ex: [('Ca', {'O': 8.0}), ('W', {'O': 4.0}), ('O', {'W': 1.0})] for CaWO4_scheelite_15586
        :param ions (list): list of ions (cations/anions) in structure.
               Ex: ['Ca', 'W'] are cations for CaWO4_scheelite_15586
        :returns cn dict with only cation-anion or anion-cation interactions.
               Ex: [('Ca', {u'O': 8.0}), ('W', {u'O': 4.0})] for cation-anion interactions of CaWO4_scheelite_15586
        """
        cns = [i for i in cns if i[0] in ions]
        for tup in cns:
            for el in tup[1].keys():
                if el in ions:
                    tup[1].pop(el)
        return cns

    def score(self, methods):
        """
        Assigns a score for how each NN method performs on a specific structure and reports all scores in a pandas
        dataframe. The score is calculated by taking the summation of the absolute value error in CN prediction
        (CN_observed - CN_expected) multiplied by the degeneracy over the number cations for each structure.

        :returns pandas df of nn algo scores
        """

        bm = self.benchmark(methods)
        nohi = [i for i in list(bm.columns) if 'HumanInterpreter' not in i]
        hi = [i for i in list(bm.columns) if 'HumanInterpreter' in i]

        # Element-wise subtraction of human interpreted cn from nn algo calculated nn = error
        # in nn algo-calculated cn (CN_observed - CN_expected). Ex: in CaF2, Ca is coordinated
        # to 8 F atoms. If nn-algo reports the coordination as 5, the error is 3 ({F: -3})
        df = {}
        for i in range(len(nohi)):
            cndict = {}
            for j in range(self.nsites):
                if str(j) in nohi[i]:
                    site = bm[nohi[i]]
                    hisite = bm[hi[j]]
                    for k in range(len(site)):
                        coord = [z for z in hisite[k].values()]
                        if all(isinstance(z, float) for z in coord) or not coord:
                            temp = Counter(site[k])
                            temp.subtract(hisite[k])
                            cndict[site.keys()[k]] = dict(temp)
                        else:
                            # For structures with several acceptable human-interpreted cn values,
                            # the nn algo-calculated cn closest to the human-interpreted cn is used.
                            # Ex: Ga can either be 4-coordinated or 12-coordinated. If an nn algo reports
                            # the coordination as being 13-coordinated, the error is 1 ({Ga: 1}).
                            t = list(site[k].values())[0]
                            lsub = []
                            dsub = {}
                            for cn in list(hisite[k].values())[0]:
                                tsub = t - cn
                                lsub.append(tsub)
                            minsub = min(map(abs, lsub))
                            dsub[list(hisite[k].keys())[0]] = minsub
                            cndict[site.keys()[k]] = dict(dsub)
                    df[nohi[i]] = dict(cndict)

        # dataframe with abs value of errors of nn-algo
        # calculated cn (|CN_observed - CN_expected|)
        for key, val in df.items():
            for i, j in val.items():
                if j != {}:
                    absval = map(abs, j.values())
                else:
                    absval = {}
                zip_absval = dict(zip(j.keys(), absval))
                val[i] = zip_absval
            df[key] = val
        df = pd.DataFrame(df)

        ts = os.path.join(module_dir, "..", "test_structures")

        # Determines total number of unique site ions (cations/anions) per site in a list.
        # Also sums number of unique site ions to get a total (int). Ex: K2SO4_beta_79777 has
        # 2 unique K atoms and 1 unique S atom. The 'unique site cations' would report
        # something like [4,4,4] since there are 4 of each unique atom (4 of 1st unique K,
        # 4 of 2nd unique K, and 4 of unique S). The 'total cations' would report 12 since
        # there is a total of 12 cations in a K2SO4_beta unit cell.
        structures = []
        for i in df.index:
            find_structure = glob.glob(os.path.join(ts, "*", i + "*"))
            s = Structure.from_file(find_structure[0])

            if "cluster" in i:
                oxi = {"Al": 3, "H": -1, "O": -2}
                s.add_oxidation_state_by_element(oxidation_states=oxi)

            structures.append(s)

        us = []
        for i in structures:
            es = SpacegroupAnalyzer(i).get_symmetrized_structure().equivalent_sites
            if self.cation_anion:
                ces = [x for x in es if '+' in x[0].species_string]
                sites = [len(x) for x in ces]
            else:
                aes = [x for x in es if '-' in x[0].species_string]
                sites = [len(x) for x in aes]
            if len(sites) < self.nsites:
                sites.extend([0] * (self.nsites - len(sites)))
            us.append(sites)

        summed = []
        for i in us:
            summed.append(sum(i))

        if self.cation_anion:
            df['unique site cations'] = us
            df['total cations'] = summed
        else:
            df['unique site anions'] = us
            df['total anions'] = summed

        # 'unique site cations/anions' and 'total cation/anions' added to dataframe
        df_cs = pd.DataFrame(df)

        # Creates dictionaries of lists of errors multiplied by ion degeneracy.
        # In the K2SO4_beta_79777 example, the list would contain 4 * the error in
        # coordination calculated for each unique site, i.e. if the error in
        # calculating the coordination for S was 1, the list would be {O: [1, 1, 1, 1]}.
        counter = 0
        for nn in df.keys()[:-2]:
            if self.cation_anion:
                num_equiv = [[num for num in equiv] for equiv in df_cs['unique site cations']]
            else:
                num_equiv = [[num for num in equiv] for equiv in df_cs['unique site anions']]
            other_counter = 0
            for j in df[nn]:
                j = j.update((x, [y]*num_equiv[other_counter][counter]) for x, y in j.items())
                other_counter += 1
                if other_counter == int(len(df.index)):
                    other_counter = 0
            counter += 1
            if counter == self.nsites:
                counter = 0

        # dataframe of error multiplied by degeneracy
        df = pd.DataFrame(df)

        # Merges all of the lists into one for that particular structure and nn algo. For
        # K2SO4_beta_79777, this list would contain 12 entries.
        merged = {}
        for m in methods:
            if m.__class__.__name__ != 'HumanInterpreter':
                algo = df[[i for i in list(df.columns) if m.__class__.__name__ in i]]
                extended = {}
                count = 0
                for a in algo[:len(algo.index)].values:
                    extension = {}
                    for i in a:
                        for key, val in i.items():
                            if key in extension.keys():
                                extension[key].extend(val)
                            else:
                                extension[key] = val
                    extended[algo.index[count]] = extension
                    count += 1
                merged[m.__class__.__name__] = dict(extended)

        # dataframe of merged error*degeneracy lists
        df = pd.DataFrame(merged)

        # sums lists from merge function.
        totsum = {}
        for algo, mats in df.items():
            matsum = {}
            for mat, coord in mats.items():
                summing = []
                for coords, li in coord.items():
                    sumli = sum(li)
                    summing.append(sumli)
                    sum_els = sum(summing)
                    matsum[mat] = sum_els
                totsum[algo] = dict(matsum)

        # dataframe of summed error * degeneracy (ints)
        df = pd.DataFrame(totsum)

        # Divides total by 'total cations/anions' to get final score.
        df = pd.concat([df, df_cs[df_cs.columns[-1:]]], axis=1)

        for m in methods:
            if m.__class__.__name__ == "HumanInterpreter":
                pass
            else:
                algo = df[m.__class__.__name__]
                if self.cation_anion:
                    div = algo.divide(df['total cations'])
                else:
                    div = algo.divide(df['total anions'])
                df[m.__class__.__name__] = div

        if self.cation_anion:
            df = df.drop('total cations', axis=1)
        else:
            df = df.drop('total anions', axis=1)

        # Adds a row that totals the error for all structures for each nn algo.
        df.loc['Total'] = df.sum(axis=0)

        #df.loc['Average'] = df.mean(axis=0)

        # dataframe with each nn algo scored using equation + total score
        df = df.round(self.nround)

        return df

class HIBase:
    __metaclass__ = abc.ABCMeta
    """
    This is an abstract base class for implementation of HumanInterpreter. 
    Compute method returns HumanInterpreter as a dictionary. 
    """

    def __init__(self, params=None):
        """
        :param params: (dict) of parameters to pass to compute method.
        """
        self._params = params if params else {}

    @abc.abstractmethod
    def compute(self, structure, n):
        """
        :param structure: (Structure) a pymatgen Structure
        :param n: (int) index of the atom in structure that the CN will be calculated for.
        :return: Dict of CN's for the site n. (e.g. {'O': 4.4, 'F': 2.1})
        """
        pass

class HumanInterpreter(HIBase):
    """
    Reads a yaml file where "human interpretations" of coordination numbers are given.
    """
    def __init__(self, custom_interpreter=None, custom_test_structures=None):

        p = os.path.join(module_dir, "..", "test_structures", "human_interpreter.yaml")

        t = os.path.join(module_dir, "..", "test_structures")
        interpreter = custom_interpreter if custom_interpreter else p
        test_structures = custom_test_structures if custom_test_structures else t

        with open(interpreter) as f:
            hi = yaml.load(f)

        for k in hi.keys():

            hi[k].append(len(hi[k]))

            if custom_test_structures:
                find_structure = glob.glob(os.path.join(test_structures, k + "*"))
            else:
                find_structure = glob.glob(os.path.join(test_structures, "*", k+"*"))
            if len(find_structure)==0:
                continue

            s = Structure.from_file(find_structure[0])
            s.remove_oxidation_states()
            hi[k].append(s)

        super(HumanInterpreter, self).__init__(params=hi)

    def compute(self, structure, n):
        for v in self._params.values():
            if structure == v[-1]:
                if len(v[:-1]) != len(v[-1]):
                    # means possibly reduced structure is used by human interpreter
                    # therefore get the equivalent sites and replace n with its
                    # index in the list of unique sites
                    es = SpacegroupAnalyzer(structure).get_symmetrized_structure().equivalent_sites
                    sites = [structure.index(x[0]) for x in es]
                    n = sites.index(n)
                if n < v[-2]:
                    cn = list(v[n].values())[0]
                    return cn
        return "null"
