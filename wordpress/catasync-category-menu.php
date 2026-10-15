<?php
/**
 * Plugin Name:       CataSync – Menu delle categorie
 * Plugin URI:        https://github.com/Giuseppe-sciarra/CataSync
 * Description:       Mette le categorie dei prodotti WooCommerce sotto la voce di menu che scegli e le tiene nello stesso ordine delle categorie, anche quando le crea o le riordina CataSync.
 * Version:           1.0.0
 * Requires at least: 6.0
 * Requires PHP:      7.4
 * Author:            Tastiere Digitali
 * Author URI:        https://tastieredigitali.it
 * License:           AGPL-3.0-or-later
 * License URI:       https://www.gnu.org/licenses/agpl-3.0.html
 * Text Domain:       catasync
 *
 * Va in wp-content/mu-plugins/ (oppure in plugins/ e si attiva come gli altri).
 *
 * Come lavora
 * - Il menu e la voce sotto cui mettere le categorie si scelgono in Aspetto → Menu categorie,
 *   oppure da CataSync (Categorie → Menu del sito). Per esempio «Prodotti» nel menu principale.
 * - Ogni categoria creata, dal sito o da CataSync, finisce sotto quella voce; le sottocategorie
 *   sotto la loro categoria (si può spegnere). Eliminata la categoria, sparisce la voce.
 * - L'ordine è quello delle categorie: WooCommerce → Prodotti → Categorie, trascinandole, oppure
 *   CataSync. Il nome della voce segue il nome della categoria.
 * - Sotto quella voce le voci delle categorie le gestisce il plugin. Le altre voci che ci metti
 *   a mano (pagine, link) restano, dopo le categorie.
 * - CataSync parla col plugin con le stesse chiavi REST di WooCommerce: /wp-json/wc-catasync/v1/menu.
 */

defined( 'ABSPATH' ) || exit;

if ( ! class_exists( 'CataSync_Menu_Categorie' ) ) :

	/**
	 * Le categorie dei prodotti, specchiate sotto una voce di menu.
	 */
	final class CataSync_Menu_Categorie {

		const VERSIONE   = '1.0.0';
		const OPZIONE    = 'catasync_menu_categorie';
		const ESITO      = 'catasync_menu_categorie_esito';
		const TASSONOMIA = 'product_cat';
		const SPAZIO     = 'wc-catasync/v1';
		const PAGINA     = 'catasync-menu-categorie';
		const SEGNO      = '_catasync_menu';

		/** Un riallineamento già messo in coda per la fine della richiesta. @var bool */
		private static $in_coda = false;

		/** Il riallineamento sta girando: i suoi effetti non ne chiedono un altro. @var bool */
		private static $al_lavoro = false;

		/**
		 * Aggancia gli eventi delle categorie, le rotte REST e la pagina in Aspetto.
		 */
		public static function avvia() {
			add_action( 'created_' . self::TASSONOMIA, array( __CLASS__, 'in_coda' ) );
			add_action( 'edited_' . self::TASSONOMIA, array( __CLASS__, 'in_coda' ) );
			add_action( 'delete_' . self::TASSONOMIA, array( __CLASS__, 'in_coda' ) );
			add_action( 'pre_delete_term', array( __CLASS__, 'prima_di_eliminare' ), 10, 2 );
			add_action( 'added_term_meta', array( __CLASS__, 'meta_cambiato' ), 10, 3 );
			add_action( 'updated_term_meta', array( __CLASS__, 'meta_cambiato' ), 10, 3 );
			add_action( 'deleted_term_meta', array( __CLASS__, 'meta_cambiato' ), 10, 3 );
			add_action( 'rest_api_init', array( __CLASS__, 'rotte' ) );
			add_filter( 'woocommerce_rest_is_request_to_rest_api', array( __CLASS__, 'chiavi_woocommerce' ) );
			add_action( 'admin_menu', array( __CLASS__, 'menu_admin' ) );
			add_action( 'admin_post_catasync_menu_salva', array( __CLASS__, 'salva_da_admin' ) );
			add_action( 'admin_post_catasync_menu_allinea', array( __CLASS__, 'allinea_da_admin' ) );
		}

		// ------------------------------------------------------------ eventi

		/**
		 * L'ordine delle categorie di WooCommerce sta nel meta «order» del termine.
		 *
		 * @param int|int[] $meta_id  Id del meta (o elenco, quando si cancella).
		 * @param int       $term_id  Termine.
		 * @param string    $meta_key Chiave.
		 */
		public static function meta_cambiato( $meta_id, $term_id, $meta_key ) {
			if ( 'order' !== $meta_key ) {
				return;
			}
			$termine = get_term( (int) $term_id );
			if ( $termine && ! is_wp_error( $termine ) && self::TASSONOMIA === $termine->taxonomy ) {
				self::in_coda();
			}
		}

		/**
		 * Prima che sparisca una categoria: le voci figlie della sua voce di menu salgono di un
		 * livello, come quando si toglie una voce da Aspetto → Menu. Senza, WordPress cancella la
		 * voce e le figlie restano appese a una voce che non c'è (e finiscono in cima al menu).
		 *
		 * @param int    $term_id  Categoria.
		 * @param string $taxonomy Tassonomia.
		 */
		public static function prima_di_eliminare( $term_id, $taxonomy ) {
			if ( self::TASSONOMIA !== $taxonomy ) {
				return;
			}
			$voci = get_posts(
				array(
					'post_type'   => 'nav_menu_item',
					'post_status' => 'any',
					'numberposts' => -1,
					'fields'      => 'ids',
					'meta_query'  => array( // phpcs:ignore WordPress.DB.SlowDBQuery.slow_db_query_meta_query
						array(
							'key'   => '_menu_item_object_id',
							'value' => (string) (int) $term_id,
						),
						array(
							'key'   => '_menu_item_object',
							'value' => self::TASSONOMIA,
						),
						array(
							'key'   => '_menu_item_type',
							'value' => 'taxonomy',
						),
					),
				)
			);
			foreach ( $voci as $voce ) {
				$padre = (string) (int) get_post_meta( $voce, '_menu_item_menu_item_parent', true );
				$figlie = get_posts(
					array(
						'post_type'   => 'nav_menu_item',
						'post_status' => 'any',
						'numberposts' => -1,
						'fields'      => 'ids',
						'meta_key'    => '_menu_item_menu_item_parent', // phpcs:ignore WordPress.DB.SlowDBQuery.slow_db_query_meta_key
						'meta_value'  => (string) $voce, // phpcs:ignore WordPress.DB.SlowDBQuery.slow_db_query_meta_value
					)
				);
				foreach ( $figlie as $figlia ) {
					update_post_meta( $figlia, '_menu_item_menu_item_parent', $padre );
				}
			}
		}

		/**
		 * Un riallineamento solo, alla fine della richiesta: riordinare trenta categorie in un
		 * colpo (o crearne una con il suo ordine) non deve rifare il menu trenta volte.
		 */
		public static function in_coda() {
			if ( self::$in_coda || self::$al_lavoro ) {
				return;
			}
			self::$in_coda = true;
			add_action( 'shutdown', array( __CLASS__, 'allinea_in_coda' ), 1 );
		}

		/**
		 * Gira a fine richiesta, se il plugin è attivo.
		 */
		public static function allinea_in_coda() {
			self::$in_coda = false;
			$impostazioni = self::impostazioni();
			if ( $impostazioni['attivo'] ) {
				self::allinea();
			}
		}

		// ------------------------------------------------------- impostazioni

		/**
		 * Impostazioni salvate, con i valori di partenza.
		 *
		 * @return array{attivo: bool, menu_id: int, voce_id: int, sottocategorie: bool}
		 */
		public static function impostazioni() {
			$salvate = get_option( self::OPZIONE, array() );
			$salvate = is_array( $salvate ) ? $salvate : array();
			return self::normalizza( $salvate );
		}

		/**
		 * Tipi giusti per ogni campo; le sottocategorie partono accese.
		 *
		 * @param array $valori Valori grezzi.
		 * @return array
		 */
		private static function normalizza( array $valori ) {
			return array(
				'attivo'         => ! empty( $valori['attivo'] ),
				'menu_id'        => isset( $valori['menu_id'] ) ? max( 0, (int) $valori['menu_id'] ) : 0,
				'voce_id'        => isset( $valori['voce_id'] ) ? max( 0, (int) $valori['voce_id'] ) : 0,
				'sottocategorie' => ! isset( $valori['sottocategorie'] ) || ! empty( $valori['sottocategorie'] ),
			);
		}

		/**
		 * Salva le impostazioni cambiate e ritorna quelle complete.
		 *
		 * @param array $nuove Solo i campi da cambiare.
		 * @return array
		 */
		public static function salva_impostazioni( array $nuove ) {
			$prima        = self::impostazioni();
			$impostazioni = self::normalizza( array_merge( $prima, $nuove ) );
			update_option( self::OPZIONE, $impostazioni, false );
			if ( $prima['menu_id'] !== $impostazioni['menu_id'] || $prima['voce_id'] !== $impostazioni['voce_id'] ) {
				self::sgombra( $prima );
			}
			return $impostazioni;
		}

		/**
		 * Cambiata la voce (o il menu): le voci di categoria che il plugin aveva messo sotto la
		 * voce di prima se ne vanno. Quelle messe a mano restano; le loro figlie salgono.
		 *
		 * @param array $prima Impostazioni di prima.
		 */
		private static function sgombra( array $prima ) {
			if ( '' !== self::problema( $prima ) ) {
				return;
			}
			$menu    = wp_get_nav_menu_object( $prima['menu_id'] );
			$voci    = self::voci( $menu );
			$sezione = self::discendenti( $prima['voce_id'], self::figli( $voci ) );
			$via     = array();
			foreach ( $sezione as $id ) {
				if ( self::e_categoria( $voci[ $id ] ) && get_post_meta( $id, self::SEGNO, true ) ) {
					$via[ $id ] = true;
				}
			}
			// Chi resta si aggancia al primo antenato che resta.
			foreach ( $sezione as $id ) {
				if ( isset( $via[ $id ] ) ) {
					continue;
				}
				$padre = $voci[ $id ]->padre;
				$passi = 0;
				while ( isset( $via[ $padre ] ) && $passi++ < 50 ) {
					$padre = $voci[ $padre ]->padre;
				}
				if ( $padre !== $voci[ $id ]->padre ) {
					update_post_meta( $id, '_menu_item_menu_item_parent', (string) $padre );
				}
			}
			foreach ( array_keys( $via ) as $id ) {
				wp_delete_post( $id, true );
			}
		}

		/**
		 * Cosa non va nella scelta di menu e voce ('' = tutto a posto).
		 *
		 * @param array $impostazioni Impostazioni da controllare.
		 * @return string
		 */
		public static function problema( array $impostazioni ) {
			if ( ! $impostazioni['menu_id'] || ! $impostazioni['voce_id'] ) {
				return __( 'Scegli il menu e la voce sotto cui mettere le categorie.', 'catasync' );
			}
			$menu = wp_get_nav_menu_object( $impostazioni['menu_id'] );
			if ( ! $menu ) {
				return __( 'Il menu scelto non esiste più.', 'catasync' );
			}
			$voci = self::voci( $menu );
			if ( ! isset( $voci[ $impostazioni['voce_id'] ] ) ) {
				return __( 'La voce scelta non è in quel menu.', 'catasync' );
			}
			if ( self::e_categoria( $voci[ $impostazioni['voce_id'] ] ) ) {
				return __( 'Scegli una voce che non sia una categoria, per esempio «Prodotti» o «Negozio».', 'catasync' );
			}
			return '';
		}

		// ------------------------------------------------------------ lettura

		/**
		 * Tutte le voci di un menu, pubblicate e no, nell'ordine del menu.
		 * Lette direttamente: wp_get_nav_menu_items() fuori dall'amministrazione scarta le voci
		 * che puntano a cose sparite, e qui servono tutte per non lasciare buchi nell'ordine.
		 *
		 * @param WP_Term $menu Menu.
		 * @return array<int, object> Id della voce => voce.
		 */
		private static function voci( $menu ) {
			$post = get_posts(
				array(
					'post_type'     => 'nav_menu_item',
					'post_status'   => 'any',
					'numberposts'   => -1,
					'orderby'       => array(
						'menu_order' => 'ASC',
						'ID'         => 'ASC',
					),
					'no_found_rows' => true,
					'tax_query'     => array( // phpcs:ignore WordPress.DB.SlowDBQuery.slow_db_query_tax_query
						array(
							'taxonomy' => 'nav_menu',
							'field'    => 'term_taxonomy_id',
							'terms'    => (int) $menu->term_taxonomy_id,
						),
					),
				)
			);
			$voci = array();
			foreach ( $post as $p ) {
				$voci[ (int) $p->ID ] = (object) array(
					'id'         => (int) $p->ID,
					'padre'      => (int) get_post_meta( $p->ID, '_menu_item_menu_item_parent', true ),
					'ordine'     => (int) $p->menu_order,
					'tipo'       => (string) get_post_meta( $p->ID, '_menu_item_type', true ),
					'oggetto'    => (string) get_post_meta( $p->ID, '_menu_item_object', true ),
					'oggetto_id' => (int) get_post_meta( $p->ID, '_menu_item_object_id', true ),
					'post'       => $p,
				);
			}
			return $voci;
		}

		/**
		 * La voce punta a una categoria dei prodotti?
		 *
		 * @param object $voce Voce del menu.
		 * @return bool
		 */
		private static function e_categoria( $voce ) {
			return 'taxonomy' === $voce->tipo && self::TASSONOMIA === $voce->oggetto;
		}

		/**
		 * Figli di ogni voce, nell'ordine attuale. Le voci con un padre che non c'è più stanno
		 * in cima, come le mostra WordPress.
		 *
		 * @param array $voci Voci del menu.
		 * @return array<int, int[]>
		 */
		private static function figli( array $voci ) {
			$figli = array();
			foreach ( $voci as $id => $voce ) {
				$padre = ( $voce->padre && $voce->padre !== $id && isset( $voci[ $voce->padre ] ) ) ? $voce->padre : 0;
				$figli[ $padre ][] = $id;
			}
			return $figli;
		}

		/**
		 * Tutte le voci sotto una voce, a qualunque livello.
		 *
		 * @param int   $voce  Voce di partenza.
		 * @param array $figli Figli per voce.
		 * @return int[]
		 */
		private static function discendenti( $voce, array $figli ) {
			$trovate = array();
			$visti   = array( $voce => true );
			$coda    = isset( $figli[ $voce ] ) ? $figli[ $voce ] : array();
			while ( $coda ) {
				$id = array_shift( $coda );
				if ( isset( $visti[ $id ] ) ) {
					continue;
				}
				$visti[ $id ] = true;
				$trovate[]    = $id;
				if ( isset( $figli[ $id ] ) ) {
					$coda = array_merge( $coda, $figli[ $id ] );
				}
			}
			return $trovate;
		}

		/**
		 * Le categorie da mettere nel menu: tutte tranne quella di riserva di WooCommerce
		 * («Senza categoria»); senza sottocategorie, solo le principali.
		 *
		 * @param bool $sottocategorie Anche le sottocategorie.
		 * @return array<int, array{id: int, padre: int, ordine: int, nome: string}>
		 */
		private static function categorie( $sottocategorie ) {
			$termini = get_terms(
				array(
					'taxonomy'               => self::TASSONOMIA,
					'hide_empty'             => false,
					'orderby'                => 'none',
					'update_term_meta_cache' => true,
				)
			);
			if ( is_wp_error( $termini ) ) {
				return array();
			}
			$riserva   = (int) get_option( 'default_product_cat', 0 );
			$categorie = array();
			foreach ( $termini as $termine ) {
				$id = (int) $termine->term_id;
				if ( $id === $riserva || ( ! $sottocategorie && $termine->parent ) ) {
					continue;
				}
				$categorie[ $id ] = array(
					'id'     => $id,
					'padre'  => (int) $termine->parent,
					'ordine' => (int) get_term_meta( $id, 'order', true ),
					'nome'   => (string) $termine->name,
				);
			}
			return $categorie;
		}

		/**
		 * Ordine fra categorie sorelle: quello di WooCommerce, poi il nome, poi l'id.
		 *
		 * @param array $a Categoria.
		 * @param array $b Categoria.
		 * @return int
		 */
		private static function confronta( $a, $b ) {
			if ( $a['ordine'] !== $b['ordine'] ) {
				return $a['ordine'] < $b['ordine'] ? -1 : 1;
			}
			$nome = strnatcasecmp( $a['nome'], $b['nome'] );
			if ( 0 !== $nome ) {
				return $nome;
			}
			return $a['id'] <=> $b['id'];
		}

		/**
		 * Le categorie dall'alto in basso: ogni madre prima delle sue figlie.
		 *
		 * @param array $categorie Categorie.
		 * @return int[]
		 */
		private static function dall_alto( array $categorie ) {
			$per_padre = array();
			foreach ( $categorie as $id => $categoria ) {
				$padre                 = isset( $categorie[ $categoria['padre'] ] ) ? $categoria['padre'] : 0;
				$per_padre[ $padre ][] = $id;
			}
			foreach ( $per_padre as &$sorelle ) {
				usort(
					$sorelle,
					function ( $a, $b ) use ( $categorie ) {
						return self::confronta( $categorie[ $a ], $categorie[ $b ] );
					}
				);
			}
			unset( $sorelle );
			$ordine = array();
			$visti  = array();
			$coda   = isset( $per_padre[0] ) ? $per_padre[0] : array();
			while ( $coda ) {
				$id = array_shift( $coda );
				if ( isset( $visti[ $id ] ) ) {
					continue;
				}
				$visti[ $id ] = true;
				$ordine[]     = $id;
				if ( isset( $per_padre[ $id ] ) ) {
					$coda = array_merge( $coda, $per_padre[ $id ] );
				}
			}
			return $ordine;
		}

		// --------------------------------------------------------- riallineo

		/**
		 * Rende la sezione del menu uguale alle categorie: voci che mancano, voci al posto
		 * sbagliato, voci di troppo, e l'ordine.
		 *
		 * @return array{aggiunte: int, spostate: int, tolte: int, riordinate: int, errore: string, quando: string}
		 */
		public static function allinea() {
			$esito        = array(
				'aggiunte'   => 0,
				'spostate'   => 0,
				'tolte'      => 0,
				'riordinate' => 0,
				'errore'     => '',
			);
			$impostazioni = self::impostazioni();
			$problema     = self::problema( $impostazioni );
			if ( '' !== $problema ) {
				$esito['errore'] = $problema;
				return self::registra( $esito );
			}

			self::$al_lavoro = true;
			try {
				$menu      = wp_get_nav_menu_object( $impostazioni['menu_id'] );
				$radice    = $impostazioni['voce_id'];
				$categorie = self::categorie( $impostazioni['sottocategorie'] );
				$voci      = self::voci( $menu );
				$sezione   = self::discendenti( $radice, self::figli( $voci ) );
				// Voci di categoria rimaste appese a una voce che non c'è più: si riprendono qui
				// (o si tolgono, se la categoria ha già la sua voce).
				foreach ( $voci as $id => $voce ) {
					if ( $voce->padre && ! isset( $voci[ $voce->padre ] ) && self::e_categoria( $voce ) && ! in_array( $id, $sezione, true ) ) {
						$sezione[] = $id;
					}
				}

				// 1. Una voce per categoria: via doppioni e voci di categorie sparite o escluse.
				$di = array();
				foreach ( $sezione as $id ) {
					$voce = $voci[ $id ];
					if ( ! self::e_categoria( $voce ) ) {
						continue;
					}
					$termine = $voce->oggetto_id;
					if ( isset( $categorie[ $termine ] ) && ! isset( $di[ $termine ] ) ) {
						$di[ $termine ] = $id;
						continue;
					}
					wp_delete_post( $id, true );
					unset( $voci[ $id ] );
					++$esito['tolte'];
				}

				// 2. Voci che mancano e voci sotto il padre sbagliato: madri prima delle figlie.
				foreach ( self::dall_alto( $categorie ) as $termine ) {
					$madre = $categorie[ $termine ]['padre'];
					$padre = ( $madre && isset( $di[ $madre ] ) ) ? $di[ $madre ] : $radice;
					if ( ! isset( $di[ $termine ] ) ) {
						$nuova = wp_update_nav_menu_item(
							$menu->term_id,
							0,
							array(
								'menu-item-object-id' => $termine,
								'menu-item-object'    => self::TASSONOMIA,
								'menu-item-type'      => 'taxonomy',
								'menu-item-parent-id' => $padre,
								'menu-item-status'    => 'publish',
								'menu-item-title'     => '', // vuoto: la voce segue il nome della categoria
							)
						);
						if ( is_wp_error( $nuova ) || ! $nuova ) {
							$esito['errore'] = is_wp_error( $nuova ) ? $nuova->get_error_message() : __( 'Voce di menu non creata.', 'catasync' );
							continue;
						}
						update_post_meta( $nuova, self::SEGNO, self::VERSIONE );
						$di[ $termine ] = (int) $nuova;
						++$esito['aggiunte'];
					} elseif ( $voci[ $di[ $termine ] ]->padre !== $padre ) {
						update_post_meta( $di[ $termine ], '_menu_item_menu_item_parent', (string) $padre );
						++$esito['spostate'];
					}
				}

				// 3. Voci messe a mano sotto una voce che non c'è più: tornano sotto la voce scelta.
				$voci = self::voci( $menu );
				foreach ( $sezione as $id ) {
					if ( isset( $voci[ $id ] ) && $voci[ $id ]->padre && ! isset( $voci[ $voci[ $id ]->padre ] ) ) {
						update_post_meta( $id, '_menu_item_menu_item_parent', (string) $radice );
						$voci[ $id ]->padre = $radice;
						++$esito['spostate'];
					}
				}

				// 4. Ordine: categorie come in WooCommerce, poi le altre voci; il resto del menu non cambia.
				$figli  = self::figli( $voci );
				$chiavi = array();
				foreach ( $di as $termine => $id ) {
					if ( isset( $voci[ $id ] ) ) {
						$chiavi[ $id ] = $categorie[ $termine ];
					}
				}
				$nella_sezione            = array_flip( self::discendenti( $radice, $figli ) );
				$nella_sezione[ $radice ] = true;
				$sequenza                 = array();
				$visti                    = array();
				self::appiattisci( 0, $figli, $chiavi, $nella_sezione, $sequenza, $visti );
				foreach ( array_keys( $voci ) as $id ) {
					if ( ! isset( $visti[ $id ] ) ) {
						$sequenza[] = $id; // anelli impossibili nel menu: in fondo, ma non persi
					}
				}
				global $wpdb;
				foreach ( $sequenza as $posizione => $id ) {
					$numero = $posizione + 1;
					if ( $voci[ $id ]->ordine !== $numero ) {
						$wpdb->update( $wpdb->posts, array( 'menu_order' => $numero ), array( 'ID' => $id ), array( '%d' ), array( '%d' ) ); // phpcs:ignore WordPress.DB.DirectDatabaseQuery
						clean_post_cache( $id );
						++$esito['riordinate'];
					}
				}

				if ( $esito['aggiunte'] || $esito['spostate'] || $esito['tolte'] || $esito['riordinate'] ) {
					// Lo stesso segnale del salvataggio da Aspetto → Menu: le cache dei menu si svuotano.
					do_action( 'wp_update_nav_menu', (int) $menu->term_id );
				}
			} finally {
				self::$al_lavoro = false;
			}
			return self::registra( $esito );
		}

		/**
		 * Visita il menu dall'alto, mettendo le voci nell'ordine finale.
		 *
		 * @param int   $padre         Voce di cui sistemare i figli (0 = primo livello).
		 * @param array $figli         Figli per voce.
		 * @param array $chiavi        Voce di categoria => categoria.
		 * @param array $nella_sezione Voci sotto la voce scelta (e la voce stessa).
		 * @param array $sequenza      Ordine finale, riempito qui.
		 * @param array $visti         Voci già messe.
		 */
		private static function appiattisci( $padre, array $figli, array $chiavi, array $nella_sezione, array &$sequenza, array &$visti ) {
			$lista = isset( $figli[ $padre ] ) ? $figli[ $padre ] : array();
			if ( isset( $nella_sezione[ $padre ] ) ) {
				$categorie = array();
				$altre     = array();
				foreach ( $lista as $id ) {
					if ( isset( $chiavi[ $id ] ) ) {
						$categorie[] = $id;
					} else {
						$altre[] = $id;
					}
				}
				usort(
					$categorie,
					function ( $a, $b ) use ( $chiavi ) {
						return self::confronta( $chiavi[ $a ], $chiavi[ $b ] );
					}
				);
				$lista = array_merge( $categorie, $altre );
			}
			foreach ( $lista as $id ) {
				if ( isset( $visti[ $id ] ) ) {
					continue;
				}
				$visti[ $id ] = true;
				$sequenza[]   = $id;
				self::appiattisci( $id, $figli, $chiavi, $nella_sezione, $sequenza, $visti );
			}
		}

		/**
		 * Tiene l'esito dell'ultimo riallineamento, per la pagina in Aspetto e per CataSync.
		 *
		 * @param array $esito Esito.
		 * @return array
		 */
		private static function registra( array $esito ) {
			$esito['quando'] = gmdate( 'c' );
			update_option( self::ESITO, $esito, false );
			return $esito;
		}

		// ---------------------------------------------------------------- stato

		/**
		 * Impostazioni, menu disponibili con le loro voci, ultimo esito.
		 *
		 * @return array
		 */
		public static function stato() {
			$impostazioni = self::impostazioni();
			$menu         = array();
			foreach ( wp_get_nav_menus() as $m ) {
				$voci   = self::voci( $m );
				$elenco = array();
				$visti  = array();
				self::elenca( 0, self::figli( $voci ), $voci, 0, $elenco, $visti );
				$menu[] = array(
					'id'   => (int) $m->term_id,
					'nome' => (string) $m->name,
					'voci' => $elenco,
				);
			}
			$ultimo = get_option( self::ESITO, array() );
			return array(
				'plugin'         => 'catasync-category-menu',
				'versione'       => self::VERSIONE,
				'attivo'         => $impostazioni['attivo'],
				'menu_id'        => $impostazioni['menu_id'],
				'voce_id'        => $impostazioni['voce_id'],
				'sottocategorie' => $impostazioni['sottocategorie'],
				'problema'       => self::problema( $impostazioni ),
				'menus'          => $menu,
				'ultimo'         => is_array( $ultimo ) && $ultimo ? $ultimo : null,
			);
		}

		/**
		 * Le voci di un menu in ordine, col livello, per le tendine di scelta.
		 *
		 * @param int   $padre   Voce di partenza.
		 * @param array $figli   Figli per voce.
		 * @param array $voci    Voci.
		 * @param int   $livello Livello attuale.
		 * @param array $elenco  Elenco riempito qui.
		 * @param array $visti   Voci già elencate.
		 */
		private static function elenca( $padre, array $figli, array $voci, $livello, array &$elenco, array &$visti ) {
			if ( empty( $figli[ $padre ] ) ) {
				return;
			}
			foreach ( $figli[ $padre ] as $id ) {
				if ( isset( $visti[ $id ] ) ) {
					continue;
				}
				$visti[ $id ] = true;
				$voce         = wp_setup_nav_menu_item( $voci[ $id ]->post );
				$titolo       = trim( wp_strip_all_tags( (string) $voce->title ) );
				$elenco[]     = array(
					'id'        => $id,
					'titolo'    => '' !== $titolo ? $titolo : __( '(senza titolo)', 'catasync' ),
					'livello'   => $livello,
					'categoria' => self::e_categoria( $voci[ $id ] ),
				);
				self::elenca( $id, $figli, $voci, $livello + 1, $elenco, $visti );
			}
		}

		// ----------------------------------------------------------------- REST

		/**
		 * GET e POST /wc-catasync/v1/menu, POST /wc-catasync/v1/menu/allinea.
		 */
		public static function rotte() {
			register_rest_route(
				self::SPAZIO,
				'/menu',
				array(
					array(
						'methods'             => WP_REST_Server::READABLE,
						'callback'            => array( __CLASS__, 'rest_stato' ),
						'permission_callback' => array( __CLASS__, 'puo' ),
					),
					array(
						'methods'             => WP_REST_Server::CREATABLE,
						'callback'            => array( __CLASS__, 'rest_salva' ),
						'permission_callback' => array( __CLASS__, 'puo' ),
						'args'                => array(
							'attivo'         => array( 'type' => 'boolean' ),
							'menu_id'        => array(
								'type'    => 'integer',
								'minimum' => 0,
							),
							'voce_id'        => array(
								'type'    => 'integer',
								'minimum' => 0,
							),
							'sottocategorie' => array( 'type' => 'boolean' ),
						),
					),
				)
			);
			register_rest_route(
				self::SPAZIO,
				'/menu/allinea',
				array(
					'methods'             => WP_REST_Server::CREATABLE,
					'callback'            => array( __CLASS__, 'rest_allinea' ),
					'permission_callback' => array( __CLASS__, 'puo' ),
				)
			);
		}

		/**
		 * Serve lo stesso permesso di Aspetto → Menu.
		 *
		 * @return bool
		 */
		public static function puo() {
			return current_user_can( 'edit_theme_options' );
		}

		/**
		 * GET /menu.
		 *
		 * @return WP_REST_Response
		 */
		public static function rest_stato() {
			return rest_ensure_response( self::stato() );
		}

		/**
		 * POST /menu: salva la scelta e, se attivo, riallinea subito.
		 *
		 * @param WP_REST_Request $richiesta Richiesta.
		 * @return WP_REST_Response|WP_Error
		 */
		public static function rest_salva( WP_REST_Request $richiesta ) {
			$nuove = array();
			foreach ( array( 'attivo', 'menu_id', 'voce_id', 'sottocategorie' ) as $campo ) {
				if ( null !== $richiesta->get_param( $campo ) ) {
					$nuove[ $campo ] = $richiesta->get_param( $campo );
				}
			}
			$prova = self::normalizza( array_merge( self::impostazioni(), $nuove ) );
			if ( $prova['attivo'] ) {
				$problema = self::problema( $prova );
				if ( '' !== $problema ) {
					return new WP_Error( 'catasync_menu', $problema, array( 'status' => 400 ) );
				}
			}
			$salvate = self::salva_impostazioni( $nuove );
			$esito   = $salvate['attivo'] ? self::allinea() : null;
			return rest_ensure_response( array( 'esito' => $esito ) + self::stato() );
		}

		/**
		 * POST /menu/allinea: riallinea adesso, anche col plugin spento.
		 *
		 * @return WP_REST_Response|WP_Error
		 */
		public static function rest_allinea() {
			$problema = self::problema( self::impostazioni() );
			if ( '' !== $problema ) {
				return new WP_Error( 'catasync_menu', $problema, array( 'status' => 400 ) );
			}
			return rest_ensure_response( array( 'esito' => self::allinea() ) + self::stato() );
		}

		/**
		 * Le chiavi REST di WooCommerce valgono anche qui. WooCommerce le accetta già per gli
		 * indirizzi /wp-json/wc-…; questo copre i siti senza permalink (?rest_route=/wc-catasync/…).
		 *
		 * @param bool $vale Decisione di WooCommerce.
		 * @return bool
		 */
		public static function chiavi_woocommerce( $vale ) {
			if ( $vale ) {
				return $vale;
			}
			$indirizzo = isset( $_SERVER['REQUEST_URI'] ) ? (string) wp_unslash( $_SERVER['REQUEST_URI'] ) : ''; // phpcs:ignore WordPress.Security.ValidatedSanitizedInput.InputNotSanitized
			$rotta     = isset( $_GET['rest_route'] ) ? (string) wp_unslash( $_GET['rest_route'] ) : ''; // phpcs:ignore WordPress.Security.NonceVerification.Recommended, WordPress.Security.ValidatedSanitizedInput.InputNotSanitized
			return false !== strpos( $indirizzo, '/' . self::SPAZIO . '/' ) || 0 === strpos( $rotta, '/' . self::SPAZIO . '/' );
		}

		// --------------------------------------------------------------- admin

		/**
		 * Aspetto → Menu categorie.
		 */
		public static function menu_admin() {
			add_theme_page(
				__( 'Menu delle categorie', 'catasync' ),
				__( 'Menu categorie', 'catasync' ),
				'edit_theme_options',
				self::PAGINA,
				array( __CLASS__, 'pagina' )
			);
		}

		/**
		 * La pagina delle impostazioni.
		 */
		public static function pagina() {
			if ( ! self::puo() ) {
				return;
			}
			$stato   = self::stato();
			$avviso  = isset( $_GET['catasync'] ) ? sanitize_key( wp_unslash( $_GET['catasync'] ) ) : ''; // phpcs:ignore WordPress.Security.NonceVerification.Recommended
			$ultimo  = $stato['ultimo'];
			$scelta  = $stato['menu_id'] . ':' . $stato['voce_id'];
			?>
			<div class="wrap">
				<h1><?php esc_html_e( 'Menu delle categorie', 'catasync' ); ?></h1>
				<p class="description" style="max-width:760px">
					<?php esc_html_e( 'Le categorie dei prodotti finiscono sotto la voce di menu che scegli qui, nello stesso ordine che hanno in Prodotti → Categorie (o in CataSync). Il nome della voce segue quello della categoria; le altre voci che metti a mano sotto quella voce restano, dopo le categorie.', 'catasync' ); ?>
				</p>

				<?php if ( 'salvato' === $avviso ) : ?>
					<div class="notice notice-success is-dismissible"><p><?php esc_html_e( 'Impostazioni salvate.', 'catasync' ); ?></p></div>
				<?php elseif ( 'allineato' === $avviso ) : ?>
					<div class="notice notice-success is-dismissible"><p><?php esc_html_e( 'Menu allineato alle categorie.', 'catasync' ); ?></p></div>
				<?php endif; ?>
				<?php if ( $stato['attivo'] && '' !== $stato['problema'] ) : ?>
					<div class="notice notice-error"><p><?php echo esc_html( $stato['problema'] ); ?></p></div>
				<?php endif; ?>

				<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
					<input type="hidden" name="action" value="catasync_menu_salva">
					<?php wp_nonce_field( 'catasync_menu_salva' ); ?>
					<table class="form-table" role="presentation">
						<tr>
							<th scope="row"><?php esc_html_e( 'Attivo', 'catasync' ); ?></th>
							<td>
								<label>
									<input type="checkbox" name="attivo" value="1" <?php checked( $stato['attivo'] ); ?>>
									<?php esc_html_e( 'Tieni il menu allineato alle categorie', 'catasync' ); ?>
								</label>
							</td>
						</tr>
						<tr>
							<th scope="row"><label for="catasync-voce"><?php esc_html_e( 'Voce del menu', 'catasync' ); ?></label></th>
							<td>
								<select id="catasync-voce" name="voce" style="min-width:320px">
									<option value=""><?php esc_html_e( '— scegli la voce —', 'catasync' ); ?></option>
									<?php foreach ( $stato['menus'] as $m ) : ?>
										<optgroup label="<?php echo esc_attr( $m['nome'] ); ?>">
											<?php foreach ( $m['voci'] as $v ) : ?>
												<option value="<?php echo esc_attr( $m['id'] . ':' . $v['id'] ); ?>"
													<?php selected( $scelta, $m['id'] . ':' . $v['id'] ); ?>
													<?php disabled( $v['categoria'] ); ?>>
													<?php echo esc_html( str_repeat( '— ', (int) $v['livello'] ) . $v['titolo'] ); ?>
												</option>
											<?php endforeach; ?>
										</optgroup>
									<?php endforeach; ?>
								</select>
								<p class="description"><?php esc_html_e( 'Le categorie vanno sotto questa voce: di solito «Prodotti» o «Negozio». Se non c\'è, creala prima in Aspetto → Menu.', 'catasync' ); ?></p>
							</td>
						</tr>
						<tr>
							<th scope="row"><?php esc_html_e( 'Sottocategorie', 'catasync' ); ?></th>
							<td>
								<label>
									<input type="checkbox" name="sottocategorie" value="1" <?php checked( $stato['sottocategorie'] ); ?>>
									<?php esc_html_e( 'Sotto la loro categoria', 'catasync' ); ?>
								</label>
								<p class="description"><?php esc_html_e( 'Spento: nel menu vanno solo le categorie principali.', 'catasync' ); ?></p>
							</td>
						</tr>
					</table>
					<?php submit_button( __( 'Salva', 'catasync' ) ); ?>
				</form>

				<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
					<input type="hidden" name="action" value="catasync_menu_allinea">
					<?php wp_nonce_field( 'catasync_menu_allinea' ); ?>
					<?php submit_button( __( 'Allinea adesso', 'catasync' ), 'secondary', 'submit', false ); ?>
					<span class="description" style="margin-left:8px"><?php esc_html_e( 'Rimette in ordine la voce scelta anche se hai toccato il menu a mano.', 'catasync' ); ?></span>
				</form>

				<?php if ( $ultimo ) : ?>
					<h2><?php esc_html_e( 'Ultimo allineamento', 'catasync' ); ?></h2>
					<p>
						<?php
						echo esc_html(
							sprintf(
								/* translators: 1: data, 2: voci aggiunte, 3: voci spostate, 4: voci tolte */
								__( '%1$s — voci aggiunte %2$d, spostate %3$d, tolte %4$d.', 'catasync' ),
								get_date_from_gmt( gmdate( 'Y-m-d H:i:s', strtotime( $ultimo['quando'] ) ), get_option( 'date_format' ) . ' ' . get_option( 'time_format' ) ),
								(int) $ultimo['aggiunte'],
								(int) $ultimo['spostate'],
								(int) $ultimo['tolte']
							)
						);
						?>
					</p>
					<?php if ( ! empty( $ultimo['errore'] ) ) : ?>
						<p style="color:#b32d2e"><?php echo esc_html( $ultimo['errore'] ); ?></p>
					<?php endif; ?>
				<?php endif; ?>
			</div>
			<?php
		}

		/**
		 * Salva dalla pagina in Aspetto.
		 */
		public static function salva_da_admin() {
			if ( ! self::puo() ) {
				wp_die( esc_html__( 'Non hai i permessi per farlo.', 'catasync' ), 403 );
			}
			check_admin_referer( 'catasync_menu_salva' );
			$scelta = isset( $_POST['voce'] ) ? sanitize_text_field( wp_unslash( $_POST['voce'] ) ) : '';
			$parti  = array_map( 'intval', array_pad( explode( ':', $scelta, 2 ), 2, 0 ) );
			$salvate = self::salva_impostazioni(
				array(
					'attivo'         => ! empty( $_POST['attivo'] ),
					'menu_id'        => $parti[0],
					'voce_id'        => $parti[1],
					'sottocategorie' => ! empty( $_POST['sottocategorie'] ),
				)
			);
			if ( $salvate['attivo'] && '' === self::problema( $salvate ) ) {
				self::allinea();
			}
			wp_safe_redirect( add_query_arg( 'catasync', 'salvato', admin_url( 'themes.php?page=' . self::PAGINA ) ) );
			exit;
		}

		/**
		 * «Allinea adesso» dalla pagina in Aspetto.
		 */
		public static function allinea_da_admin() {
			if ( ! self::puo() ) {
				wp_die( esc_html__( 'Non hai i permessi per farlo.', 'catasync' ), 403 );
			}
			check_admin_referer( 'catasync_menu_allinea' );
			self::allinea();
			wp_safe_redirect( add_query_arg( 'catasync', 'allineato', admin_url( 'themes.php?page=' . self::PAGINA ) ) );
			exit;
		}
	}

	CataSync_Menu_Categorie::avvia();

endif;
