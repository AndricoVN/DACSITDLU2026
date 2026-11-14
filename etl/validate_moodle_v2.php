<?php
define('CLI_SCRIPT',true); define('NO_MOODLE_COOKIES',true);
require('/var/www/html/config.php');
if ($CFG->dbname!=='moodledb_v2' || $CFG->wwwroot!=='http://localhost:8081') {throw new RuntimeException('Wrong target');}
$credentials=json_decode(stream_get_contents(STDIN),true,512,JSON_THROW_ON_ERROR);
$pdo=new PDO('mysql:host='.$CFG->dbhost.';dbname=moodledb_v2;charset=utf8mb4',$CFG->dbuser,$CFG->dbpass,[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$checks=[];
function check_v2($name,$condition) {global $checks; $checks[$name]=(bool)$condition; if(!$condition) {throw new RuntimeException('Failed check: '.$name);} }
$checks['no_email']=(bool)$CFG->noemailever;
$checks['no_cron']=!$CFG->cron_enabled;
check_v2('cookie_isolated',$CFG->sessioncookie==='V2');
check_v2('native_install_complete',!during_initial_install());
$readversion=function() use($CFG) {require($CFG->dirroot.'/version.php'); return $version;};
check_v2('schema_matches_code',(string)get_config(null,'version')===(string)$readversion());
check_v2('raw_tables_complete',(int)$pdo->query('SELECT COUNT(*) FROM v2_raw_manifest WHERE complete=1')->fetchColumn()===17);
check_v2('source_users_preserved',(int)$pdo->query('SELECT COUNT(*) FROM user')->fetchColumn()===(int)$pdo->query('SELECT COUNT(*) FROM v2_raw_user')->fetchColumn()+(int)$pdo->query('SELECT COUNT(*) FROM v2_technical_users')->fetchColumn());
check_v2('no_visible_learning_courses',(int)$pdo->query('SELECT COUNT(*) FROM course WHERE id<>1 AND visible<>0')->fetchColumn()===0);
check_v2('no_fabricated_activities',(int)$pdo->query('SELECT COUNT(*) FROM course_modules')->fetchColumn()===0);
check_v2('enrolments_accounted_for',(int)$pdo->query('SELECT COUNT(*) FROM user_enrolments')->fetchColumn()+(int)$pdo->query("SELECT COUNT(*) FROM v2_quarantine WHERE entity='user_enrolments'")->fetchColumn()===(int)$pdo->query('SELECT COUNT(*) FROM v2_raw_user_enrolments')->fetchColumn());
check_v2('no_orphan_native_enrolment',(int)$pdo->query('SELECT COUNT(*) FROM user_enrolments ue LEFT JOIN user u ON ue.userid=u.id LEFT JOIN enrol e ON ue.enrolid=e.id WHERE u.id IS NULL OR e.id IS NULL')->fetchColumn()===0);
check_v2('no_orphan_native_course_context',(int)$pdo->query('SELECT COUNT(*) FROM course c LEFT JOIN context x ON x.contextlevel=50 AND x.instanceid=c.id WHERE x.id IS NULL')->fetchColumn()===0);
check_v2('no_orphan_native_role_assignment',(int)$pdo->query('SELECT COUNT(*) FROM role_assignments r LEFT JOIN context c ON c.id=r.contextid LEFT JOIN user u ON u.id=r.userid LEFT JOIN role t ON t.id=r.roleid WHERE c.id IS NULL OR u.id IS NULL OR t.id IS NULL')->fetchColumn()===0);
$error=0;
$admin=authenticate_user_login($credentials['admin_username'],$credentials['admin_password'],false,$error);
check_v2('technical_admin_authenticates',$admin && is_siteadmin($admin->id));
$sample=$pdo->query("SELECT id,username FROM user WHERE deleted=0 AND suspended=0 AND username REGEXP '^[0-9]{7}$' AND id IN (SELECT userid FROM user_enrolments) ORDER BY id LIMIT 1")->fetch(PDO::FETCH_ASSOC);
check_v2('student_sample_exists',(bool)$sample);
$student=authenticate_user_login($sample['username'],$sample['username'],false,$error);
check_v2('student_demo_password_authenticates',$student && (int)$student->id===(int)$sample['id']);
check_v2('student_not_admin',!is_siteadmin($student->id));
check_v2('student_has_no_system_role',(int)$pdo->query('SELECT COUNT(*) FROM role_assignments r JOIN context c ON c.id=r.contextid WHERE c.contextlevel=10 AND r.userid='.(int)$student->id)->fetchColumn()===0);
check_v2('wrong_password_rejected',!authenticate_user_login($sample['username'],'not-the-demo-password-987!',false,$error));
$blocked=false;
try {$pdo->query('SELECT COUNT(*) FROM moodledb.user');} catch(PDOException $e) {$blocked=true;}
check_v2('app_cannot_read_old_database',$blocked);
check_v2('guest_infrastructure_present',(int)$pdo->query("SELECT COUNT(*) FROM user WHERE username='guest'")->fetchColumn()===1);
check_v2('technical_users_do_not_overlap_source',(int)$pdo->query('SELECT COUNT(*) FROM v2_technical_users t JOIN v2_raw_user r ON r.source_id=t.userid')->fetchColumn()===0);
// Private stdout goes only to the orchestrator; sample credentials must not be logged.
echo json_encode(['checks'=>$checks,'student_username'=>$sample['username']],JSON_THROW_ON_ERROR);
