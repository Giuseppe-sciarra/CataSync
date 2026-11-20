<?php
/**
 * Plugin Name:       CataSync – Svuota cache
 * Plugin URI:        https://github.com/Giuseppe-sciarra/CataSync
 * Description:       Quando CataSync cambia prodotti, prezzi o categorie, cancella le pagine di questo sito dalla cache FastCGI di Nginx, così i clienti vedono subito le modifiche.
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
 * - Non fa niente da solo: lo chiama CataSync, poco dopo aver scritto sul sito, se nelle
 *   impostazioni del sito su CataSync lo svuotamento è acceso.
 * - CataSync manda la cartella della cache (per esempio /www/server/fastcgi_cache). Se non la
 *   manda, si usa RT_WP_NGINX_HELPER_CACHE_PATH del wp-config.php, se c'è.
 * - La cartella può essere in comune con altri siti dello stesso server: si cancellano solo i
 *   file la cui chiave (la riga «KEY:» che Nginx scrive in ogni file) è di questo sito. I file
 *   senza quella riga non si toccano mai, anche se la cartella è sbagliata.
 * - PHP deve poter leggere e cancellare nella cartella: se il sito ha open_basedir, la cartella
 *   dev'esserci dentro.
 * - Rotta: POST /wp-json/wc-catasync-cache/v1/svuota, con le chiavi WooCommerce di un
 *   amministratore o gestore del negozio.
 */

defined( 'ABSPATH' ) || exit;

if ( ! class_exists( 'CataSync_Svuota_Cache' ) ) :

	/**
	 * Cancella dalla cache FastCGI di Nginx le pagine di questo sito.
	 */
	final class CataSync_Svuota_Cache {

		const VERSIONE = '1.0.0';
		const SPAZIO   = 'wc-catasync-cache/v1';
		const TESTA    = 8192; // byte letti di ogni file: l'intestazione di Nginx con la chiave sta lì.

		public static function avvia() {
			add_action( 'rest_api_init', array( __CLASS__, 'rotte' ) );
			add_filter( 'woocommerce_rest_is_request_to_rest_api', array( __CLASS__, 'chiavi_woocommerce' ) );
		}

		public static function rotte() {
			register_rest_route(
				self::SPAZIO,
				'/svuota',
				array(
					'methods'             => 'POST',
					'callback'            => array( __CLASS__, 'rest_svuota' ),
					'permission_callback' => array( __CLASS__, 'puo' ),
					'args'                => array(
						'percorso' => array(
							'type'    => 'string',
							'default' => '',
						),
					),
				)
			);
		}

		public static function puo() {
			return current_user_can( 'manage_woocommerce' ) || current_user_can( 'manage_options' );
		}

		/**
		 * Le chiavi WooCommerce valgono per le rotte che iniziano con wc-: con i permalink lo
		 * vede WooCommerce dall'indirizzo, con ?rest_route= no, e lo si dice qui.
		 */
		public static function chiavi_woocommerce( $vale ) {
			if ( $vale ) {
				return $vale;
			}
			$indirizzo = isset( $_SERVER['REQUEST_URI'] ) ? (string) wp_unslash( $_SERVER['REQUEST_URI'] ) : ''; // phpcs:ignore WordPress.Security.ValidatedSanitizedInput.InputNotSanitized
			$rotta     = isset( $_GET['rest_route'] ) ? (string) wp_unslash( $_GET['rest_route'] ) : ''; // phpcs:ignore WordPress.Security.NonceVerification.Recommended, WordPress.Security.ValidatedSanitizedInput.InputNotSanitized
			return false !== strpos( $indirizzo, '/' . self::SPAZIO . '/' ) || 0 === strpos( $rotta, '/' . self::SPAZIO . '/' );
		}

		public static function rest_svuota( WP_REST_Request $richiesta ) {
			$percorso = trim( (string) $richiesta->get_param( 'percorso' ) );
			if ( '' === $percorso && defined( 'RT_WP_NGINX_HELPER_CACHE_PATH' ) ) {
				$percorso = (string) RT_WP_NGINX_HELPER_CACHE_PATH;
			}
			$esito = self::svuota( $percorso, self::host() );
			if ( is_wp_error( $esito ) ) {
				return $esito;
			}
			return rest_ensure_response( $esito );
		}

		/**
		 * Il nome del sito com'è nelle chiavi di Nginx ($host è sempre minuscolo), senza www.
		 */
		public static function host() {
			$host = strtolower( (string) wp_parse_url( home_url(), PHP_URL_HOST ) );
			return 0 === strpos( $host, 'www.' ) ? substr( $host, 4 ) : $host;
		}

		/**
		 * Cancella i file della cache la cui chiave è di $host. Restituisce i conteggi o un errore.
		 *
		 * @param string $percorso Cartella della cache FastCGI.
		 * @param string $host     Nome del sito, minuscolo e senza www.
		 * @return array|WP_Error
		 */
		public static function svuota( $percorso, $host ) {
			if ( '' === $percorso ) {
				return self::errore( 'Cartella della cache non indicata: impostala su CataSync, nelle impostazioni del sito.' );
			}
			if ( '/' !== $percorso[0] || false !== strpos( $percorso, '..' ) ) {
				return self::errore( sprintf( 'Il percorso della cache «%s» dev\'essere assoluto, senza «..».', $percorso ) );
			}
			$cartella = @realpath( $percorso ); // phpcs:ignore WordPress.PHP.NoSilencedErrors.Discouraged -- open_basedir avvisa e basta
			if ( false === $cartella || ! @is_dir( $cartella ) || ! @is_readable( $cartella ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors.Discouraged
				return self::errore( sprintf( 'La cartella della cache «%s» non esiste o PHP non la può leggere: controlla il percorso e che sia dentro open_basedir del sito.', $percorso ) );
			}
			if ( '/' === $cartella ) {
				return self::errore( 'La cartella della cache non può essere la radice del disco.' );
			}
			if ( '' === $host ) {
				return self::errore( 'Non riesco a ricavare il nome del sito da home_url.' );
			}

			ignore_user_abort( true );
			if ( function_exists( 'set_time_limit' ) ) {
				@set_time_limit( 300 ); // phpcs:ignore WordPress.PHP.NoSilencedErrors.Discouraged
			}

			// Il nome sta all'inizio della chiave, dopo schema e metodo se ci sono, e prima della porta
			// o della pagina: «httpsGETesempio.it/…», «GEThttpsesempio.it/…», «https://esempio.it/…»,
			// «esempio.it/…». Ancorato all'inizio, perché «altro.it/esempio.it/» non è di questo sito.
			$schema = '~^(?:[A-Z]+)?(?:https?)?(?:://)?(?:[A-Z]+)?(?:www\.)?' . preg_quote( $host, '~' ) . '(?::\d+)?(?:[/?]|$)~';
			$conti  = array(
				'percorso'       => $cartella,
				'host'           => $host,
				'file'           => 0,
				'letti'          => 0,
				'svuotati'       => 0,
				'non_cancellati' => 0,
			);

			try {
				$giro = new RecursiveIteratorIterator(
					new RecursiveDirectoryIterator( $cartella, FilesystemIterator::SKIP_DOTS ),
					RecursiveIteratorIterator::LEAVES_ONLY,
					RecursiveIteratorIterator::CATCH_GET_CHILD
				);
				foreach ( $giro as $file ) {
					if ( ! $file->isFile() || $file->isLink() ) {
						continue;
					}
					++$conti['file'];
					$chiave = self::chiave( $file->getPathname() );
					if ( null === $chiave ) {
						continue;
					}
					++$conti['letti'];
					if ( ! preg_match( $schema, $chiave ) ) {
						continue;
					}
					if ( @unlink( $file->getPathname() ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors.Discouraged, WordPress.WP.AlternativeFunctions.unlink_unlink
						++$conti['svuotati'];
					} elseif ( file_exists( $file->getPathname() ) ) {
						++$conti['non_cancellati']; // se nel frattempo l'ha tolto Nginx, va bene così
					}
				}
			} catch ( Exception $e ) {
				return self::errore( sprintf( 'Non riesco a leggere la cartella della cache «%s»: %s', $percorso, $e->getMessage() ) );
			}

			if ( $conti['file'] > 0 && 0 === $conti['letti'] ) {
				return self::errore( sprintf( 'In «%s» non ci sono file della cache di Nginx (manca la riga KEY): il percorso è sbagliato? Non ho cancellato niente.', $percorso ) );
			}
			if ( $conti['non_cancellati'] > 0 && 0 === $conti['svuotati'] ) {
				return self::errore( sprintf( 'Ho trovato %d pagine del sito nella cache ma PHP non le può cancellare: i file sono di un altro utente.', $conti['non_cancellati'] ) );
			}
			return $conti;
		}

		/**
		 * La chiave di un file della cache di Nginx (la riga «KEY: …»), o null se non è un file di Nginx.
		 */
		private static function chiave( $nome ) {
			$f = @fopen( $nome, 'rb' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors.Discouraged, WordPress.WP.AlternativeFunctions.file_system_operations_fopen
			if ( ! $f ) {
				return null;
			}
			$testa = (string) fread( $f, self::TESTA ); // phpcs:ignore WordPress.WP.AlternativeFunctions.file_system_operations_fread
			fclose( $f ); // phpcs:ignore WordPress.WP.AlternativeFunctions.file_system_operations_fclose
			$da = strpos( $testa, "\nKEY: " );
			if ( false === $da ) {
				return null;
			}
			$da += 6;
			$a   = strpos( $testa, "\n", $da );
			return false === $a ? null : substr( $testa, $da, $a - $da );
		}

		private static function errore( $messaggio ) {
			return new WP_Error( 'catasync_cache', $messaggio, array( 'status' => 400 ) );
		}
	}

	CataSync_Svuota_Cache::avvia();

endif;
