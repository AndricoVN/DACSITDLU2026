<?php
// CLI only. Raw snapshots remain authoritative; operational courses are hidden shells.
define('CLI_SCRIPT', true);
define('NO_MOODLE_COOKIES', true);
require('/var/www/html/config.php');
if ($CFG->dbname !== 'moodledb_v2' || $CFG->wwwroot !== 'http://localhost:8081') {
    throw new RuntimeException('Refusing non-v2 target');
}
require_once($CFG->dirroot . '/course/lib.php');
$pdo = new PDO('mysql:host='.$CFG->dbhost.';dbname=moodledb_v2;charset=utf8mb4',
    $CFG->dbuser, $CFG->dbpass, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
$pdo->exec("CREATE TABLE IF NOT EXISTS v2_build_state (phase VARCHAR(64) PRIMARY KEY, details LONGTEXT NOT NULL) ENGINE=InnoDB");
$pdo->exec("CREATE TABLE IF NOT EXISTS v2_quarantine (entity VARCHAR(64), source_id BIGINT, reason VARCHAR(100), PRIMARY KEY(entity,source_id,reason)) ENGINE=InnoDB");
$pdo->exec("CREATE TABLE IF NOT EXISTS v2_category_provenance (categoryid BIGINT PRIMARY KEY, source_available TINYINT NOT NULL) ENGINE=InnoDB");
function raw_batches($name) {
    global $pdo;
    $allowed=['user','course','course_categories','course_sections','enrol','user_enrolments'];
    if (!in_array($name,$allowed,true)) { throw new RuntimeException('Unknown raw table'); }
    $last=-1;
    do {
        $stmt=$pdo->prepare('SELECT source_id,row_json FROM v2_raw_'.$name.' WHERE source_id>? ORDER BY source_id LIMIT 1000');
        $stmt->execute([$last]); $batch=$stmt->fetchAll(PDO::FETCH_ASSOC); $stmt->closeCursor();
        foreach ($batch as $row) { $last=(int)$row['source_id']; yield json_decode($row['row_json'],true,512,JSON_THROW_ON_ERROR); }
    } while ($batch);
}
function textval($v) { return in_array($v,['NULL','\\N'],true)?'':$v; }
function intval_source($v) {
    if (!preg_match('/^-?[0-9]+$/',(string)$v)) { throw new RuntimeException('Invalid numeric mapping'); }
    return (int)$v;
}
function insert_named($table,$data) {
    global $pdo;
    static $cache=[];
    $key=$table.':'.implode(',',array_keys($data));
    if (!isset($cache[$key])) {
        $cols=implode(',',array_map(fn($v)=>'`'.$v.'`',array_keys($data)));
        $cache[$key]=$pdo->prepare('INSERT INTO `'.$table.'` ('.$cols.') VALUES ('.implode(',',array_fill(0,count($data),'?')).')');
    }
    $cache[$key]->execute(array_values($data));
}
function quarantine($table,$id,$reason) {
    global $pdo;
    $s=$pdo->prepare('INSERT IGNORE INTO v2_quarantine VALUES (?,?,?)'); $s->execute([$table,$id,$reason]);
}
function phase($name,$fn) {
    global $pdo;
    $s=$pdo->prepare('SELECT details FROM v2_build_state WHERE phase=?'); $s->execute([$name]);
    $old=$s->fetchColumn();
    if ($old!==false) { return json_decode($old,true); }
    $pdo->beginTransaction();
    try {
        $result=$fn();
        $s=$pdo->prepare('INSERT INTO v2_build_state VALUES (?,?)'); $s->execute([$name,json_encode($result,JSON_THROW_ON_ERROR)]);
        $pdo->commit(); echo $name.': complete'.PHP_EOL; flush(); return $result;
    } catch (Throwable $e) { if ($pdo->inTransaction()) { $pdo->rollBack(); } throw $e; }
}
$required=['user','course','course_categories','course_scope','course_modules','course_sections','enrol','user_enrolments','assign','assign_submission','assign_grades','grade_items','grade_grades','modules','role','role_assignments','context'];
foreach ($required as $name) {
    $s=$pdo->prepare('SELECT complete FROM v2_raw_manifest WHERE table_name=?'); $s->execute(['v2_raw_'.$name]);
    if ($s->fetchColumn()!=1) { throw new RuntimeException('Incomplete raw snapshot'); }
}
$users=phase('users',function() {
    global $pdo,$CFG;
    if ((int)$pdo->query('SELECT COUNT(*) FROM user')->fetchColumn()!==2) { throw new RuntimeException('Expected clean install users'); }
    $adminid=(int)$pdo->query('SELECT MAX(source_id) FROM v2_raw_user')->fetchColumn()+100;
    $admin=$pdo->query('SELECT * FROM user WHERE id IN (SELECT CAST(value AS UNSIGNED) FROM config WHERE name=\'siteadmins\')')->fetch(PDO::FETCH_ASSOC);
    if (!$admin) { throw new RuntimeException('Clean admin missing'); }
    $admin['id']=$adminid;
    $pdo->exec('DELETE FROM user_preferences'); $pdo->exec('DELETE FROM user_password_history');
    $pdo->exec('DELETE FROM context WHERE contextlevel=30');
    $pdo->exec('DELETE FROM user');
    insert_named('user',$admin);
    $s=$pdo->prepare("UPDATE config SET value=? WHERE name='siteadmins'"); $s->execute([(string)$adminid]);
    $count=0;
    foreach (raw_batches('user') as $r) {
        if (count($r)!==15 || !in_array($r[2],['0','1'],true) || !in_array($r[3],['0','1'],true) || $r[4]!=='1') {
            quarantine('user',(int)$r[0],'unverified_layout_or_flags'); continue;
        }
        // Low bcrypt cost ONLY for this loopback-only disposable demo, not production.
        $data=['id'=>(int)$r[0],'auth'=>'manual','confirmed'=>1,'deleted'=>(int)$r[2],
            'suspended'=>(int)$r[3],'mnethostid'=>(int)$CFG->mnet_localhost_id,
            'username'=>$r[5],'password'=>password_hash($r[5],PASSWORD_BCRYPT,['cost'=>4]),
            'idnumber'=>textval($r[6]),'email'=>$r[7],'firstname'=>$r[8],'lastname'=>$r[9],
            'firstaccess'=>intval_source($r[10]),'lastaccess'=>intval_source($r[11]),
            'lastlogin'=>intval_source($r[12]),'timecreated'=>intval_source($r[14]),'timemodified'=>0];
        // UNKNOWN source column 13 is deliberately not assigned a meaning.
        try { insert_named('user',$data); $count++; }
        catch (PDOException $e) {
            if (($e->errorInfo[1]??0)===1062) { quarantine('user',(int)$r[0],'duplicate_operational_identity'); }
            else { throw $e; }
        }
    }
    return ['real_users'=>$count,'technical_admin_id'=>$adminid];
});
$CFG->siteadmins=(string)$users['technical_admin_id'];
$pdo->exec("CREATE TABLE IF NOT EXISTS v2_technical_users (userid BIGINT PRIMARY KEY, purpose VARCHAR(64) NOT NULL UNIQUE) ENGINE=InnoDB");
$s=$pdo->prepare("INSERT IGNORE INTO v2_technical_users VALUES (?,'admin')"); $s->execute([$users['technical_admin_id']]);
$guest=phase('technical_guest',function() {
    global $pdo,$CFG;
    $existing=$pdo->query("SELECT id FROM user WHERE username='guest'")->fetchColumn();
    if($existing!==false) {return ['id'=>(int)$existing,'technical'=>false];}
    // No guest identity was exported. Moodle requires this infrastructure account.
    $id=(int)$pdo->query('SELECT MAX(id) FROM user')->fetchColumn()+1;
    insert_named('user',['id'=>$id,'username'=>'guest','auth'=>'manual','password'=>'not cached',
        'confirmed'=>1,'mnethostid'=>(int)$CFG->mnet_localhost_id,'firstname'=>'Guest','lastname'=>'User',
        'email'=>'guest@example.invalid','deleted'=>0,'suspended'=>0]);
    insert_named('v2_technical_users',['userid'=>$id,'purpose'=>'guest']);
    return ['id'=>$id,'technical'=>true];
});
set_config('siteguest',$guest['id']);
phase('categories',function() {
    global $pdo;
    if ((int)$pdo->query('SELECT COUNT(*) FROM course')->fetchColumn()!==1) { throw new RuntimeException('Expected clean courses'); }
    $known=[]; $ids=[];
    foreach(raw_batches('course_categories') as $r) {
        if (count($r)!==14) { throw new RuntimeException('Category layout mismatch'); }
        $known[(int)$r[0]]=$r; $ids[(int)$r[0]]=true;
        if ((int)$r[5]>0) {$ids[(int)$r[5]]=true;}
    }
    foreach(raw_batches('course') as $r) { if ((int)$r[1]>0) {$ids[(int)$r[1]]=true;} }
    $pdo->exec('DELETE FROM course_categories'); $pdo->exec('DELETE FROM context WHERE contextlevel=40');
    $paths=[]; $parents=[];
    foreach ($ids as $id=>$_) { $parents[$id]=isset($known[$id])?(int)$known[$id][5]:0; }
    $resolve=function($id,$seen=[]) use (&$resolve,&$paths,&$parents) {
        if (isset($paths[$id])) {return $paths[$id];}
        if (isset($seen[$id]) || !isset($parents[$id]) || count($seen)>30) { throw new RuntimeException('Category cycle/unresolved parent'); }
        $seen[$id]=true; $p=$parents[$id];
        return $paths[$id]=($p?$resolve($p,$seen):'').'/'.$id;
    };
    foreach ($ids as $id=>$_) {
        $r=$known[$id]??null; $path=$resolve($id);
        insert_named('course_categories',['id'=>$id,'name'=>$r?$r[1]:'Danh mục #'.$id.' (thiếu dữ liệu)',
            'idnumber'=>$r?textval($r[2]):'','description'=>$r?textval($r[3]):'',
            'descriptionformat'=>$r?(int)$r[4]:1,'parent'=>$parents[$id],
            'sortorder'=>$id,'visible'=>0,'visibleold'=>$r?(int)$r[9]:0,
            'depth'=>substr_count($path,'/'),'path'=>$path,'timemodified'=>$r?(int)$r[10]:0]);
        insert_named('v2_category_provenance',['categoryid'=>$id,'source_available'=>$r?1:0]);
    }
    return ['source_categories'=>count($known),'technical_placeholders'=>count($ids)-count($known)];
});
phase('courses',function() {
    global $pdo;
    $count=0;
    foreach(raw_batches('course') as $r) {
        if(count($r)!==36) { throw new RuntimeException('Course layout mismatch'); }
        $id=(int)$r[0];
        if ($id===(int)SITEID) { continue; } // The site front page is technical, raw source retained.
        // Hidden shells: original visibility/format/full metadata remain in raw.
        insert_named('course',['id'=>$id,'category'=>(int)$r[1],'sortorder'=>(int)$r[2],
            'fullname'=>$r[3],'shortname'=>$r[4],'idnumber'=>textval($r[5]),
            'summary'=>'','summaryformat'=>1,'format'=>'topics','showgrades'=>0,
            'startdate'=>intval_source($r[11]),'enddate'=>intval_source($r[12]),
            'visible'=>0,'visibleold'=>0,'timecreated'=>0,'timemodified'=>0]);
        $count++;
    }
    return ['hidden_real_course_shells'=>$count];
});
phase('sections',function() {
    global $pdo;
    $courses=array_fill_keys($pdo->query('SELECT id FROM course')->fetchAll(PDO::FETCH_COLUMN),true);
    $seen=[]; $count=0;
    foreach(raw_batches('course_sections') as $r) {
        if(count($r)!==10) { throw new RuntimeException('Section layout mismatch'); }
        if (!isset($courses[(int)$r[1]])) { quarantine('course_sections',(int)$r[0],'missing_course'); continue; }
        $s=$pdo->prepare('SELECT COUNT(*) FROM course_sections WHERE id=?'); $s->execute([(int)$r[0]]);
        if($s->fetchColumn()) { throw new RuntimeException('Section conflicts with clean site scaffold'); }
        insert_named('course_sections',['id'=>(int)$r[0],'course'=>(int)$r[1],'section'=>(int)$r[2],
            'name'=>in_array($r[3],['NULL','\\N'],true)?null:$r[3], 'summary'=>'','summaryformat'=>1,
            'sequence'=>'','visible'=>0,'availability'=>null,'timemodified'=>(int)$r[9]]);
        $seen[(int)$r[1].':'.(int)$r[2]]=true; $count++;
    }
    // Core-generated section zero is infrastructure, not a fabricated learning activity.
    foreach($courses as $course=>$_) {
        $s=$pdo->prepare('SELECT COUNT(*) FROM course_sections WHERE course=? AND section=0');$s->execute([$course]);
        if(!$s->fetchColumn()) {insert_named('course_sections',['course'=>$course,'section'=>0,'name'=>null,'summary'=>'','summaryformat'=>1,'sequence'=>'','visible'=>0]);}
    }
    return ['real_section_metadata'=>$count,'module_sequences_cleared'=>true];
});
// Create native contexts through Moodle, never copy source context IDs/paths.
phase('contexts',function() {
    global $DB;
    foreach($DB->get_records('course_categories',null,'depth ASC,id ASC','id') as $r) { \context_coursecat::instance($r->id); }
    foreach($DB->get_records('course',null,'id ASC','id') as $r) { \context_course::instance($r->id); }
    return ['native_contexts_rebuilt'=>true];
});
phase('enrolments',function() {
    global $pdo,$DB;
    $role=$DB->get_record('role',['shortname'=>'student'],'id',MUST_EXIST);
    $courses=array_fill_keys($pdo->query('SELECT id FROM course')->fetchAll(PDO::FETCH_COLUMN),true);
    $users=array_fill_keys($pdo->query('SELECT id FROM user')->fetchAll(PDO::FETCH_COLUMN),true);
    $methods=[]; $count=0;
    foreach(raw_batches('enrol') as $r) {
        if(count($r)!==35) { throw new RuntimeException('Enrol layout mismatch'); }
        if(!isset($courses[(int)$r[3]])) { quarantine('enrol',(int)$r[0],'missing_course');continue; }
        insert_named('enrol',['id'=>(int)$r[0],'enrol'=>'manual','status'=>(int)$r[2],
            'courseid'=>(int)$r[3],'sortorder'=>(int)$r[4],'name'=>null,'roleid'=>$role->id,
            'enrolperiod'=>(int)$r[6],'enrolstartdate'=>(int)$r[7],'enrolenddate'=>(int)$r[8]]);
        $methods[(int)$r[0]]=true;
    }
    foreach(raw_batches('user_enrolments') as $r) {
        if(count($r)!==9) { throw new RuntimeException('User enrolment layout mismatch'); }
        if(!isset($methods[(int)$r[2]]) || !isset($users[(int)$r[3]])) { quarantine('user_enrolments',(int)$r[0],'missing_enrol_or_user');continue; }
        insert_named('user_enrolments',['id'=>(int)$r[0],'status'=>(int)$r[1],'enrolid'=>(int)$r[2],
            'userid'=>(int)$r[3],'timestart'=>(int)$r[4],'timeend'=>(int)$r[5],
            'modifierid'=>0,'timecreated'=>(int)$r[7],'timemodified'=>(int)$r[8]]);
        $count++;
    }
    $pdo->exec("INSERT INTO role_assignments (roleid,contextid,userid,timemodified,modifierid,component,itemid,sortorder)
        SELECT ".(int)$role->id.",c.id,u.userid,u.timemodified,0,'enrol_manual',e.id,0
        FROM user_enrolments u JOIN enrol e ON e.id=u.enrolid
        JOIN context c ON c.contextlevel=50 AND c.instanceid=e.courseid");
    return ['valid_user_enrolments'=>$count,'operational_method'=>'manual','original_methods_preserved_in_raw'=>true];
});
set_config('noemailever',1); set_config('cron_enabled',0); set_config('guestloginbutton',0);
set_config('enrol_plugins_enabled','manual'); set_config('auth','manual');
set_config('registerauth',''); set_config('enablewebservices',0);
set_config('timezone','Asia/Ho_Chi_Minh');
purge_all_caches();
$admin=$DB->get_record('user',['id'=>$users['technical_admin_id']],'*',MUST_EXIST);
$USER=$admin;
\context_user::instance($admin->id);
$report=['database'=>'moodledb_v2','web_ready'=>false,
    'operational_mode'=>'hidden course shells; source snapshots authoritative; no activity/file reconstruction claim',
    'users'=>(int)$pdo->query('SELECT COUNT(*) FROM user')->fetchColumn(),
    'courses'=>(int)$pdo->query('SELECT COUNT(*) FROM course')->fetchColumn(),
    'hidden_courses'=>(int)$pdo->query('SELECT COUNT(*) FROM course WHERE id<>1 AND visible=0')->fetchColumn(),
    'activities'=>(int)$pdo->query('SELECT COUNT(*) FROM course_modules')->fetchColumn(),
    'quarantine'=>$pdo->query('SELECT entity,reason,COUNT(*) AS count FROM v2_quarantine GROUP BY entity,reason')->fetchAll(PDO::FETCH_ASSOC),
    'phases'=>$pdo->query('SELECT phase,details FROM v2_build_state ORDER BY phase')->fetchAll(PDO::FETCH_ASSOC),
    'email_disabled'=>(bool)$CFG->noemailever,'cron_disabled'=>!$CFG->cron_enabled];
echo json_encode($report,JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT|JSON_THROW_ON_ERROR).PHP_EOL;
