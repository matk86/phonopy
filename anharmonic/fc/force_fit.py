import sys
import numpy as np
from phonopy.harmonic.force_constants import similarity_transformation, get_rotated_displacement, get_rotated_forces, get_positions_sent_by_rot_inv
from anharmonic.phonon3.displacement_fc3 import get_bond_symmetry, get_reduced_site_symmetry

class FC3Fit:
    def __init__(self,
                 supercell,
                 disp_dataset,
                 symmetry):

        self._scell = supercell
        self._lattice = supercell.get_cell().T
        self._positions = supercell.get_scaled_positions()
        self._num_atom = len(self._positions)
        self._dataset = disp_dataset
        self._symmetry = symmetry
        self._symprec = symmetry.get_symmetry_tolerance()
        
        self._fc2 = np.zeros((self._num_atom, self._num_atom, 3, 3),
                             dtype='double')
        self._fc3 = np.zeros((self._num_atom, self._num_atom, self._num_atom,
                              3, 3, 3), dtype='double')

    def run(self):
        self._get_matrices()

    def _get_matrices(self):
        self._get_matrices_1st(self._dataset)

    def _get_matrices_1st(self, dataset):
        unique_first_atom_nums = np.unique(
            [x['number'] for x in dataset['first_atoms']])
        
        for first_atom_num in unique_first_atom_nums:
            datasets_1st = []
            for dataset_1st in dataset['first_atoms']:
                if first_atom_num != dataset_1st['number']:
                    continue
                datasets_1st.append(dataset_1st)
            self._get_matrices_2nd(datasets_1st)

    def _get_matrices_2nd(self, datasets_1st):
        for dataset_1st in datasets_1st:
            self._expand_forces_of_2nd_displacements(dataset_1st)
        first_atom_num = datasets_1st[0]['number']
        site_symmetry = self._symmetry.get_site_symmetry(first_atom_num)

        for second_atom_num in range(self._num_atom):
            rot_disps = None
            rot_forces = None
            for dataset_1st in datasets_1st:
                disp1 = dataset_1st['displacement']
                disp_pairs = []
                sets_of_forces = []
                for dataset_2nd in dataset_1st['second_atoms']:
                    if second_atom_num != dataset_2nd['number']:
                        continue
                    disp_pairs.append([disp1, dataset_2nd['displacement']])
                    sets_of_forces.append(dataset_2nd['forces'])
    
                # rot_disps1 (Num-pairs x num-site-syms, 3)
                # rot_disps2 (Num-pairs x num-site-syms, 3)
                # rot_disps_pair (Num-pairs x num-site-syms, 9)
                (rot_disps1,
                 rot_disps2,
                 rot_disps_pair) = self._rotate_displacements(disp_pairs,
                                                              site_symmetry)
                ones = np.ones(rot_disps1.shape[0]).reshape((-1, 1))
                rot_disps_tmp = np.hstack(
                    (ones, rot_disps1, rot_disps2, rot_disps_pair))
                rot_forces_tmp = self._create_force_matrix(sets_of_forces,
                                                           site_symmetry,
                                                           first_atom_num)
                if rot_disps is None:
                    rot_disps = rot_disps_tmp
                else:
                    rot_disps = np.vstack((rot_disps, rot_disps_tmp))

                if rot_forces is None:
                    rot_forces = rot_forces_tmp
                else:
                    rot_forces = np.vstack((rot_forces, rot_forces_tmp))
            
            fc = self._solve(rot_disps, rot_forces, len(datasets_1st))

            print "d", rot_disps.shape, "f", rot_forces.shape, "fc", fc.shape
            fc2 = fc[:, 1:4, :].reshape((self._num_atom, 3, 3))
            self._fc2[first_atom_num] += fc2
            fc3 = fc[:, 7:, :].reshape((self._num_atom, 3, 3, 3))
            self._fc3[first_atom_num, second_atom_num] = fc3

        self._fc2[first_atom_num] /= self._num_atom
        from anharmonic.file_IO import write_fc3_dat
        write_fc3_dat(self._fc3, 'fc3-oneshot.dat')

    def _create_force_matrix(self,
                             sets_of_forces,
                             site_symmetry,
                             disp_atom_num):
        site_syms_cart = [similarity_transformation(self._lattice, sym)
                          for sym in site_symmetry]

        positions = (self._positions.copy() -
                     self._positions[disp_atom_num])
        rot_map_syms = get_positions_sent_by_rot_inv(positions,
                                                     site_symmetry,
                                                     self._symprec)
        force_matrix = []
        for i in range(self._num_atom):
            for forces in sets_of_forces:
                force_matrix.append(
                    get_rotated_forces(forces[rot_map_syms[:, i]],
                                       site_syms_cart))
        return np.reshape(force_matrix, (self._num_atom, -1, 3))
        
    def _solve(self, rot_disps, rot_forces, num_disp1):
        inv_disps = np.linalg.pinv(rot_disps)
        fc = []

        for i in range(self._num_atom):
            forces = None
            for j in range(num_disp1):
                if forces is None:
                    forces = rot_forces[j]
                else:
                    forces = np.vstack((forces, rot_forces[j]))
            fc.append(-np.dot(inv_disps, forces))
        
        return np.array(fc)

    def _rotate_displacements(self, disp_pairs, site_syms):
        rot_disps1 = []
        rot_disps2 = []
        rot_disps_pair = []

        for (u1, u2) in disp_pairs:
            for ssym in site_syms:
                ssym_c = similarity_transformation(self._lattice, ssym)
                Su1 = np.dot(ssym_c, u1)
                Su2 = np.dot(ssym_c, u2)
                rot_disps1.append(Su1)
                rot_disps2.append(Su2)
                rot_disps_pair.append(np.outer(Su1, Su2))
        return (np.reshape(rot_disps1, (-1, 3)),
                np.reshape(rot_disps2, (-1, 3)),
                np.reshape(rot_disps_pair, (-1, 9)))

    def _expand_forces_of_2nd_displacements(self, dataset_1st):
        first_atom_num = dataset_1st['number']
        site_symmetry = self._symmetry.get_site_symmetry(first_atom_num)
        disp1 = dataset_1st['displacement']
        direction = np.dot(np.linalg.inv(self._lattice), disp1)
        reduced_site_sym = get_reduced_site_symmetry(
            site_symmetry, direction, self._symprec)
        second_atom_nums = [x['number'] for x in dataset_1st['second_atoms']]
        unique_second_atom_nums = np.unique(np.array(second_atom_nums))
        positions = self._positions.copy() - self._positions[first_atom_num]
        rot_map_syms = get_positions_sent_by_rot_inv(positions,
                                                     reduced_site_sym,
                                                     self._symprec)

        new_datasets = []
        for i in range(self._num_atom):
            if i in unique_second_atom_nums:
                continue

            sym_cart = None
            orig_atom_num = None
            for j, sym in enumerate(reduced_site_sym):
                if rot_map_syms[j, i] in unique_second_atom_nums:
                    sym_cart = similarity_transformation(self._lattice, sym)
                    orig_atom_num = rot_map_syms[j, i]
                    break

            assert sym_cart is not None, "Something is wrong."

            for dataset_2nd in dataset_1st['second_atoms']:
                if orig_atom_num != dataset_2nd['number']:
                    continue
                
                forces = np.double([
                        np.dot(sym_cart, f) for f in dataset_2nd['forces']])
                disp = np.dot(sym_cart, dataset_2nd['displacement'])
                              
                new_datasets.append({'forces': forces,
                                     'displacement': disp,
                                     'number': i})
        dataset_1st['second_atoms'] += new_datasets
            
    
            
class FC2Fit:
    def __init__(self,
                 supercell,
                 disp_dataset,
                 symmetry):

        self._scell = supercell
        self._lattice = supercell.get_cell().T
        self._positions = supercell.get_scaled_positions()
        self._num_atom = len(self._positions)
        self._dataset = disp_dataset
        self._symmetry = symmetry
        self._symprec = symmetry.get_symmetry_tolerance()
        
        self._fc2 = np.zeros((self._num_atom, self._num_atom, 3, 3),
                             dtype='double')

    def run(self):
        self._get_matrices()
        print self._fc2

    def _get_matrices(self):
        unique_first_atom_nums = np.unique(
            [x['number'] for x in self._dataset['first_atoms']])
        
        for first_atom_num in unique_first_atom_nums:
            disps = []
            sets_of_forces = []
            for dataset_1st in self._dataset['first_atoms']:
                if first_atom_num != dataset_1st['number']:
                    continue
                disps.append(dataset_1st['displacement'])
                sets_of_forces.append(dataset_1st['forces'])

            site_symmetry = self._symmetry.get_site_symmetry(first_atom_num)
            site_sym_cart = [similarity_transformation(self._lattice, sym)
                             for sym in site_symmetry]
            rot_disps = get_rotated_displacement(disps, site_sym_cart)
            rot_forces = self._create_force_matrix(sets_of_forces,
                                                   site_symmetry,
                                                   site_sym_cart,
                                                   first_atom_num)
            self._fc2[first_atom_num, :] = self._solve(rot_disps, rot_forces)

    def _create_force_matrix(self,
                             sets_of_forces,
                             site_symmetry,
                             site_sym_cart,
                             disp_atom_num):
        positions = (self._positions.copy() -
                     self._positions[disp_atom_num])
        rot_map_syms = get_positions_sent_by_rot_inv(positions,
                                                     site_symmetry,
                                                     self._symprec)
        force_matrix = []
        for i in range(self._num_atom):
            for forces in sets_of_forces:
                force_matrix.append(
                    get_rotated_forces(forces[rot_map_syms[:, i]],
                                       site_sym_cart))
        return np.reshape(force_matrix, (self._num_atom, -1, 3))
        
    def _solve(self, rot_disps, rot_forces):
        inv_disps = np.linalg.pinv(rot_disps)
        return [-np.dot(inv_disps, f) for f in rot_forces]
        
            